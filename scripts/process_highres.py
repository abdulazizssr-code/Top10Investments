#!/usr/bin/env python3
"""
High-res crop extraction for Top 10 Holdings from fund fact sheet PDFs.
Converts PDF pages to high-res PNGs before sending to Claude Code CLI
for much better accuracy on chart/Arabic text reading.

Usage:
  python process_highres.py                     # 4 workers (default)
  python process_highres.py --workers 6         # 6 workers
  python process_highres.py --rebuild            # Rebuild main JSON from parts
  python process_highres.py --limit 10           # Process only 10 PDFs
  python process_highres.py --retry-errors       # Retry previously failed PDFs
"""

import json, subprocess, os, re, argparse, time, glob, shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Event

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF not installed. Run: pip install PyMuPDF")
    exit(1)

# ─── Config ───────────────────────────────────────────────────────
PDFS_DIR = "pdfs"
OUTPUT_FILE = "fund_data_claude_code.json"
ERROR_LOG = "processing_errors.json"
RESULTS_DIR = "results_parts"
TEMP_IMG_DIR = "temp_images"  # Inside project dir so Claude Code can read them
TIMEOUT_SECONDS = 180  # Slightly longer for image processing
DPI = 250              # High-res but not excessive (250 DPI = ~2x normal)
MAX_PAGES_TO_SEND = 4  # Max pages to send per PDF
file_lock = Lock()
rate_limit_hit = Event()

parser = argparse.ArgumentParser()
parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers")
parser.add_argument("--retry-errors", action="store_true", help="Retry failed PDFs")
parser.add_argument("--limit", type=int, default=0, help="Max PDFs to process")
parser.add_argument("--rebuild", action="store_true", help="Rebuild main JSON from parts")
parser.add_argument("--quarters", nargs="*", default=["Q4-2025", "Q1-2026"],
                    help="Which quarters to process (default: Q4-2025 Q1-2026)")
parser.add_argument("--all-quarters", action="store_true", help="Process all quarters")
args = parser.parse_args()

os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(TEMP_IMG_DIR, exist_ok=True)

# ─── Date-to-quarter mapping ─────────────────────────────────────
def date_to_quarter(date_str):
    """Convert PDF filename date to quarter. Jan-Mar→Q4 prev year, etc."""
    m = re.search(r'(\d{4})-(\d{2})', date_str)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if month <= 3:
        return f"Q4-{year-1}"
    elif month <= 6:
        return f"Q1-{year}"
    elif month <= 9:
        return f"Q2-{year}"
    else:
        return f"Q3-{year}"

# ─── Scan PDF folders ────────────────────────────────────────────
def scan_pdfs():
    """Scan all fund folders for English PDFs and build work list."""
    items = []
    if not os.path.isdir(PDFS_DIR):
        print(f"ERROR: {PDFS_DIR}/ directory not found!")
        exit(1)

    for fund_dir in sorted(os.listdir(PDFS_DIR)):
        fund_path = os.path.join(PDFS_DIR, fund_dir)
        if not os.path.isdir(fund_path):
            continue

        fund_name = fund_dir.replace('_', ' ')
        fund_key = fund_dir.lower().replace(' ', '_').replace('-', '_')

        for pdf_file in sorted(os.listdir(fund_path)):
            if not pdf_file.lower().endswith('.pdf'):
                continue
            # Prefer English PDFs
            if not (pdf_file.lower().endswith('_en.pdf') or pdf_file.lower().endswith('_en .pdf')):
                continue

            quarter = date_to_quarter(pdf_file)
            if not quarter:
                continue

            pdf_full = os.path.join(fund_path, pdf_file)
            items.append({
                "fund_key": fund_key,
                "fund": fund_name,
                "quarter": quarter,
                "path": pdf_full
            })

    return items


# ─── Rebuild from parts ──────────────────────────────────────────
def rebuild_from_parts():
    """Rebuild the main JSON from individual part files."""
    results = {"funds": {}}
    part_files = glob.glob(os.path.join(RESULTS_DIR, "*.json"))
    loaded = 0
    for pf in part_files:
        try:
            part = json.load(open(pf, 'r', encoding='utf-8'))
            fk = part['fund_key']
            q = part['quarter']
            holdings = part['holdings']
            if fk not in results['funds']:
                results['funds'][fk] = {"id": fk, "name": part['fund'], "quarters": {}}
            results['funds'][fk]['quarters'][q] = {
                "period": q.replace("-", " "),
                "source": "claude_code",
                "holdings": holdings
            }
            loaded += 1
        except:
            pass
    return results, loaded

if args.rebuild:
    results, count = rebuild_from_parts()
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    total_q = sum(len(f['quarters']) for f in results['funds'].values())
    print(f"Rebuilt from {count} parts: {len(results['funds'])} funds, {total_q} quarters")
    exit(0)

# ─── Load existing results ───────────────────────────────────────
if os.path.exists(OUTPUT_FILE):
    try:
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            results = json.load(f)
    except:
        print("  Main JSON corrupted, rebuilding from parts...")
        results, _ = rebuild_from_parts()
else:
    results = {"funds": {}}

# Merge any parts not in main file
part_files = glob.glob(os.path.join(RESULTS_DIR, "*.json"))
parts_added = 0
for pf in part_files:
    try:
        part = json.load(open(pf, 'r', encoding='utf-8'))
        fk = part['fund_key']
        q = part['quarter']
        if fk not in results['funds'] or q not in results['funds'][fk].get('quarters', {}):
            if fk not in results['funds']:
                results['funds'][fk] = {"id": fk, "name": part['fund'], "quarters": {}}
            results['funds'][fk]['quarters'][q] = {
                "period": q.replace("-", " "),
                "source": "claude_code",
                "holdings": part['holdings']
            }
            parts_added += 1
    except:
        pass

if parts_added:
    print(f"  Recovered {parts_added} quarters from parts not in main file")

if os.path.exists(ERROR_LOG):
    try:
        with open(ERROR_LOG, 'r', encoding='utf-8') as f:
            error_log = json.load(f)
    except:
        error_log = {}
else:
    error_log = {}

# ─── Build work list ─────────────────────────────────────────────
all_items = scan_pdfs()
target_quarters = set(args.quarters) if not args.all_quarters else None

work = []
for item in all_items:
    fk, q = item['fund_key'], item['quarter']
    ek = f"{fk}|{q}"

    # Filter by target quarters
    if target_quarters and q not in target_quarters:
        continue

    # Skip already done (for target quarters, we RE-EXTRACT for accuracy)
    # But keep the part file logic for resume capability
    part_path = os.path.join(RESULTS_DIR, f"{fk}__{q}_hires.json")
    if os.path.exists(part_path):
        continue  # Already done with high-res method

    if not args.retry_errors and ek in error_log:
        continue

    if os.path.exists(item['path']):
        work.append({**item, 'error_key': ek})

if args.limit:
    work = work[:args.limit]

total_existing = sum(1 for f in glob.glob(os.path.join(RESULTS_DIR, "*_hires.json")))
print(f"High-res done: {total_existing} | To process: {len(work)} PDFs | Workers: {args.workers}")
print(f"Target quarters: {', '.join(sorted(target_quarters)) if target_quarters else 'ALL'}")
print(f"DPI: {DPI} | Max pages per PDF: {MAX_PAGES_TO_SEND}\n")

if len(work) == 0:
    print("Nothing to process!")
    exit(0)

# ─── Create high-res PDF from selected pages ─────────────────────
def create_highres_pdf(pdf_path, out_path):
    """Re-render selected PDF pages at high DPI into a new compact PDF."""
    doc = fitz.open(pdf_path)
    num_pages = len(doc)

    # Score pages to find Top 10 section
    candidate_pages = []
    for pn in range(num_pages):
        page = doc[pn]
        text = page.get_text().lower()
        score = 0
        if 'top 10' in text or 'top ten' in text:
            score += 100
        if 'holding' in text:
            score += 50
        if 'invest' in text:
            score += 30
        if '%' in page.get_text():
            score += 20
        if any(w in text for w in ['weight', 'allocation', 'portfolio', 'composition']):
            score += 25
        imgs = page.get_images(full=True)
        if len(imgs) >= 2:
            score += 15
        candidate_pages.append((pn, score))

    candidate_pages.sort(key=lambda x: -x[1])

    if candidate_pages[0][1] == 0:
        pages_to_use = list(range(min(num_pages, MAX_PAGES_TO_SEND)))
    else:
        pages_to_use = [p[0] for p in candidate_pages[:MAX_PAGES_TO_SEND]]
        pages_to_use.sort()

    # Create new PDF with high-res rendered pages (JPEG compressed)
    new_doc = fitz.open()
    for pn in pages_to_use:
        page = doc[pn]
        # Render page at high DPI
        mat = fitz.Matrix(DPI / 72, DPI / 72)
        pix = page.get_pixmap(matrix=mat)

        # Create a new page sized to the pixmap
        img_rect = fitz.Rect(0, 0, pix.width * 72 / DPI, pix.height * 72 / DPI)
        new_page = new_doc.new_page(width=img_rect.width, height=img_rect.height)

        # Insert as JPEG for much smaller file size (~500KB vs ~17MB per page)
        img_bytes = pix.tobytes('jpeg', jpg_quality=85)
        new_page.insert_image(img_rect, stream=img_bytes)

    new_doc.save(out_path, deflate=True, garbage=4)
    new_doc.close()
    doc.close()
    return len(pages_to_use)


# ─── Worker function ──────────────────────────────────────────────
def process_one(item):
    if rate_limit_hit.is_set():
        return item, None, "SKIPPED_RATE_LIMIT"

    fk = item['fund_key']
    q = item['quarter']
    pdf = item['path']

    # Create temp high-res PDF inside project dir
    tmp_dir = os.path.join(TEMP_IMG_DIR, f"{fk}__{q}")
    os.makedirs(tmp_dir, exist_ok=True)
    hires_pdf = os.path.join(tmp_dir, "hires.pdf")

    try:
        # Step 1: Create high-res PDF from selected pages
        n_pages = create_highres_pdf(pdf, hires_pdf)
        if n_pages == 0:
            return item, None, "No pages converted"

        # Step 2: Ask Claude Code to read the high-res PDF (proven method)
        prompt = (
            f'Read the file at {hires_pdf} and extract the Top 10 Holdings/Investments. '
            f'Read ALL company names very carefully - they may be in Arabic or English. '
            f'If names are in Arabic, translate them to English company names. '
            f'Return ONLY a JSON object like: '
            f'{{"top_10_holdings":[{{"rank":1,"name":"Company Name","weight_percent":10.5}}]}} '
            f'Read from bar charts, pie charts, or tables. English names preferred. '
            f'Be very precise with company names - do not guess or approximate.'
        )

        result = subprocess.run(
            ['claude', '-p', prompt, '--max-turns', '5'],
            capture_output=True, text=True,
            timeout=TIMEOUT_SECONDS,
            cwd=os.getcwd(), shell=True
        )
        output = result.stdout.strip()

        if not output:
            return item, None, "Empty output"

        # Detect rate limit
        if "out of" in output.lower() or "usage" in output.lower() or "resets" in output.lower():
            rate_limit_hit.set()
            return item, None, f"RATE_LIMIT: {output[:80]}"

        # Parse JSON response
        cleaned = re.sub(r'```(?:json)?\s*', '', output).strip().rstrip('`')

        for key in ['top_10_holdings', 'holdings', 'top10']:
            pattern = r'\{[^{}]*"' + key + r'"\s*:\s*\[.*?\]\s*\}'
            match = re.search(pattern, cleaned, re.DOTALL)
            if match:
                data = json.loads(match.group())
                raw = data.get(key, [])
                if raw:
                    holdings = []
                    for idx, h in enumerate(raw):
                        holdings.append({
                            "rank": h.get('rank', h.get('r', idx + 1)),
                            "name": h.get('name', h.get('n', 'Unknown')),
                            "ticker": h.get('ticker', h.get('t', '')),
                            "weight": h.get('weight_percent', h.get('weight', h.get('w', 0)))
                        })
                    return item, holdings, None

        # Fallback: try parsing as plain JSON
        try:
            data = json.loads(cleaned)
            for key in ['top_10_holdings', 'holdings']:
                if key in data and data[key]:
                    holdings = []
                    for idx, h in enumerate(data[key]):
                        holdings.append({
                            "rank": h.get('rank', h.get('r', idx + 1)),
                            "name": h.get('name', h.get('n', 'Unknown')),
                            "ticker": h.get('ticker', h.get('t', '')),
                            "weight": h.get('weight_percent', h.get('weight', h.get('w', 0)))
                        })
                    return item, holdings, None
        except:
            pass

        return item, None, f"No JSON ({len(output)} chars): {output[:100]}"

    except subprocess.TimeoutExpired:
        return item, None, "Timeout"
    except Exception as e:
        return item, None, str(e)
    finally:
        # Clean up temp images for this PDF
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except:
            pass


# ─── Run parallel ────────────────────────────────────────────────
processed = errors = skipped = 0

with ThreadPoolExecutor(max_workers=args.workers) as pool:
    futures = {pool.submit(process_one, item): item for item in work}

    for future in as_completed(futures):
        item, holdings, err = future.result()
        fk = item['fund_key']
        q = item['quarter']
        ek = item['error_key']

        if err == "SKIPPED_RATE_LIMIT":
            skipped += 1
            continue

        if holdings:
            # Save as individual part file with _hires suffix
            part_path = os.path.join(RESULTS_DIR, f"{fk}__{q}_hires.json")
            with open(part_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "fund_key": fk, "fund": item['fund'], "quarter": q,
                    "holdings": holdings, "method": "highres_crop"
                }, f, ensure_ascii=False, indent=2)

            # Also save as the standard part file (overwrite old extraction)
            std_path = os.path.join(RESULTS_DIR, f"{fk}__{q}.json")
            with open(std_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "fund_key": fk, "fund": item['fund'], "quarter": q,
                    "holdings": holdings
                }, f, ensure_ascii=False, indent=2)

            with file_lock:
                if fk not in results['funds']:
                    results['funds'][fk] = {"id": fk, "name": item['fund'], "quarters": {}}
                results['funds'][fk]['quarters'][q] = {
                    "period": q.replace("-", " "),
                    "source": "claude_code",
                    "holdings": holdings
                }
                error_log.pop(ek, None)

            processed += 1
            print(f"  OK: {item['fund'][:45]} {q} ({len(holdings)} holdings)")
        else:
            if err and err.startswith("RATE_LIMIT"):
                print(f"\n  RATE LIMIT HIT! Stopping...")
                skipped += 1
            else:
                errors += 1
                with file_lock:
                    error_log[ek] = {"error": err, "path": item['path'],
                                     "fund": item['fund'], "quarter": q}
                print(f"  FAIL: {item['fund'][:45]} {q} - {err[:80]}")

# ─── Final save ──────────────────────────────────────────────────
results, total_parts = rebuild_from_parts()
with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
with open(ERROR_LOG, 'w', encoding='utf-8') as f:
    json.dump(error_log, f, ensure_ascii=False, indent=2)

total_q = sum(len(f['quarters']) for f in results['funds'].values())

# Clean up temp_images directory
try:
    shutil.rmtree(TEMP_IMG_DIR, ignore_errors=True)
except:
    pass

print(f"\n{'=' * 60}")
print(f"  DONE: {processed} new, {errors} failed, {skipped} skipped (rate limit)")
print(f"  TOTAL: {total_q} quarters from {total_parts} part files")
if skipped > 0:
    print(f"  Re-run when usage resets: python process_highres.py --workers {args.workers}")
print(f"{'=' * 60}")
