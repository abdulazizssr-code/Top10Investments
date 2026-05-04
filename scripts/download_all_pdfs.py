#!/usr/bin/env python3
"""
Tadawul Equity Fund PDF Bulk Downloader
========================================
Downloads ALL quarterly report (Fact Sheet) PDFs for every public equity fund
on Tadawul (Saudi Exchange).

Usage:
    python download_all_pdfs.py                  # Download all PDFs (requires Selenium)
    python download_all_pdfs.py --test           # Test with 3 funds only
    python download_all_pdfs.py --list           # Just list funds, no download
    python download_all_pdfs.py --fund "Riyad"   # Only funds matching name

Requirements:
    pip install selenium webdriver-manager requests
"""

import os
import re
import sys
import json
import time
import base64
import logging
import argparse
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_URL = "https://www.saudiexchange.sa"
SCRIPT_DIR = Path(__file__).parent
PDF_DIR = SCRIPT_DIR / "pdfs"
LOG_FILE = SCRIPT_DIR / "download_log.json"

LISTING_URL = f"{BASE_URL}/wps/portal/saudiexchange/ourmarkets/funds-market-watch/mutual-funds?locale=en"
EQUITY_KEYWORDS = ['equity', 'etf', 'stock', 'shares']

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("PDFDownloader")

def scrape_fund_list(driver):
    """Scrape the list of equity funds from Tadawul's Mutual Funds Summary page.

    Uses Selenium to load the page, then extracts all fund links with their
    full URLs. This ensures we always have fresh, working URLs since Tadawul's
    WebSphere Portal regenerates navigation state tokens periodically.

    Returns list of [name, manager, full_url] for equity funds only.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    log.info("Step 1: Loading Mutual Funds listing page...")
    driver.get(LISTING_URL)

    # Wait for the table to load
    try:
        WebDriverWait(driver, 20).until(
            lambda d: len(d.find_elements(By.CSS_SELECTOR, 'table a[href*="company-profile-mutual-fund"]')) > 10
        )
    except Exception:
        log.warning("  Table may not have fully loaded, proceeding anyway...")

    time.sleep(3)  # Extra wait for all rows to render

    # Extract all fund links
    links = driver.find_elements(By.CSS_SELECTOR, 'table a[href*="company-profile-mutual-fund"]')
    log.info(f"  Found {len(links)} total fund links on listing page")

    all_funds = []
    for link in links:
        try:
            name = link.text.strip()
            href = link.get_attribute('href')
            if not name or not href:
                continue
            # Get the fund manager from the next cell in the row
            row = link.find_element(By.XPATH, './ancestor::tr')
            cells = row.find_elements(By.TAG_NAME, 'td')
            manager = cells[1].text.strip() if len(cells) > 1 else ''
            all_funds.append([name, manager, href])
        except Exception:
            continue

    # Filter for equity funds only
    equity_funds = []
    for name, manager, href in all_funds:
        name_lower = name.lower()
        if any(kw in name_lower for kw in EQUITY_KEYWORDS):
            equity_funds.append([name, manager, href])

    log.info(f"  Filtered to {len(equity_funds)} equity funds")
    return equity_funds


def fetch_fund_page_selenium(driver, url, fund_name):
    """Fetch a fund page using Selenium (for JS-rendered pages).

    Waits for the page to fully render, including the Fact Sheet section
    which is loaded dynamically by Tadawul's SPA.
    """
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        driver.get(url)

        # Wait up to 15 seconds for the page body to have substantial content
        try:
            WebDriverWait(driver, 15).until(
                lambda d: len(d.page_source) > 5000
            )
        except Exception:
            log.warning(f"  Page may not have fully loaded for {fund_name}")

        # Try to find and click the "Fact Sheet" tab/section if it exists
        try:
            # Look for various possible tab/link text
            for tab_text in ["Fact Sheet", "FactSheet", "fact sheet", "Quarterly Report"]:
                try:
                    tabs = driver.find_elements(By.XPATH,
                        f"//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{tab_text.lower()}')]"
                    )
                    for tab in tabs:
                        if tab.is_displayed():
                            tab.click()
                            time.sleep(2)  # Wait for tab content to load
                            break
                except Exception:
                    pass
        except Exception:
            pass

        # Additional wait for PDF links to appear in the DOM
        time.sleep(3)

        # Scroll down to trigger any lazy-loaded content
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)

        html = driver.page_source
        log.info(f"  Page loaded: {len(html):,} chars")
        return html
    except Exception as e:
        log.error(f"  Selenium failed for {fund_name}: {e}")
        return None


def extract_pdf_links(html):
    """Extract factsheet PDF links from a fund page HTML.

    Strategy: prefer English PDFs, but include Arabic if no English version
    exists for a given quarter. This ensures we never miss a report period.

    Returns (selected_factsheets, selected_announcements) where each is a list
    of PDF paths with full coverage across all available quarters.
    """
    # Look for factsheet PDF links
    factsheet_pattern = r'href=["\'](/Resources/mfpdfs/factsheet/[^"\']+\.pdf)'
    factsheets = re.findall(factsheet_pattern, html, re.IGNORECASE)

    # Also check for announcement-linked PDFs (different path)
    announcement_pattern = r'href=["\'](/Resources/fsPdf/[^"\']+\.pdf)'
    announcements = re.findall(announcement_pattern, html, re.IGNORECASE)

    selected_factsheets = _select_best_pdfs(factsheets)
    selected_announcements = _select_best_pdfs(announcements)

    return selected_factsheets, selected_announcements


def _select_best_pdfs(pdf_list):
    """From a list of PDFs, pick English where available, Arabic otherwise.

    Groups PDFs by quarter (derived from the date in the filename).
    For each quarter: if an English version exists, use it; otherwise use Arabic.
    This guarantees no quarter is skipped just because it lacks an English report.
    """
    from collections import defaultdict

    # Group by quarter
    quarter_map = defaultdict(list)  # quarter -> list of pdf paths
    ungrouped = []

    for pdf_path in pdf_list:
        q = pdf_to_quarter(pdf_path)
        if q == "Unknown":
            ungrouped.append(pdf_path)
        else:
            quarter_map[q].append(pdf_path)

    selected = []
    for quarter, pdfs in quarter_map.items():
        english = [p for p in pdfs if p.lower().endswith('_en.pdf')]
        arabic = [p for p in pdfs if p.lower().endswith('_ar.pdf')]

        if english:
            selected.append(english[0])  # Use English version
        elif arabic:
            selected.append(arabic[0])   # Fallback to Arabic
            log.info(f"    Quarter {quarter}: no English version, using Arabic")
        else:
            # Neither _En nor _Ar suffix — take whatever is available
            selected.append(pdfs[0])
            log.info(f"    Quarter {quarter}: unknown language variant, using {pdfs[0].split('/')[-1]}")

    return selected


def pdf_to_quarter(pdf_path):
    """Extract quarter info from PDF filename date.

    Filename pattern: {fundId}_{type}_{date}_{time}_{lang}.pdf
    Date maps to quarter: Jan-Mar → Q4 prev year, Apr-Jun → Q1, Jul-Sep → Q2, Oct-Dec → Q3
    """
    match = re.search(r'_(\d{4})-(\d{2})-(\d{2})_', pdf_path)
    if not match:
        return "Unknown"
    year, month = int(match.group(1)), int(match.group(2))

    if 1 <= month <= 3:
        return f"Q4-{year - 1}"
    elif 4 <= month <= 6:
        return f"Q1-{year}"
    elif 7 <= month <= 9:
        return f"Q2-{year}"
    else:
        return f"Q3-{year}"


def download_pdf_requests(pdf_url, save_path):
    """Download a single PDF file using requests (fallback, may get blocked)."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': BASE_URL,
    })
    try:
        resp = session.get(pdf_url, timeout=60, stream=True)
        if resp.status_code == 200:
            content_type = resp.headers.get('Content-Type', '')
            if 'html' in content_type.lower():
                log.warning(f"  Got HTML instead of PDF for {pdf_url}")
                return False
            with open(save_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        else:
            log.warning(f"  HTTP {resp.status_code} downloading {pdf_url}")
            return False
    except Exception as e:
        log.error(f"  Download failed: {e}")
        return False


def download_pdf_selenium(driver, pdf_url, save_path, download_dir):
    """Download a PDF using Selenium by navigating to the URL.

    Chrome is configured to auto-download PDFs to download_dir.
    We wait for the file to appear, then move it to save_path.
    """
    import shutil
    import glob as glob_mod

    # Get list of existing files in download dir before download
    before = set(os.listdir(download_dir))

    try:
        driver.get(pdf_url)
    except Exception as e:
        log.error(f"  Selenium navigate failed: {e}")
        return False

    # Wait for the new file to appear (up to 30 seconds)
    new_file = None
    for _ in range(60):
        time.sleep(0.5)
        after = set(os.listdir(download_dir))
        new_files = after - before
        # Filter out .crdownload (partial) and .tmp files
        completed = [f for f in new_files if not f.endswith('.crdownload') and not f.endswith('.tmp')]
        if completed:
            new_file = completed[0]
            break
        # Check if there's a .crdownload file (download in progress)
        in_progress = [f for f in new_files if f.endswith('.crdownload')]
        if in_progress:
            continue  # Still downloading, keep waiting

    if not new_file:
        # One more check — sometimes the filename matches what we expect
        expected_name = save_path.name
        expected_in_dl = Path(download_dir) / expected_name
        if expected_in_dl.exists():
            new_file = expected_name
        else:
            log.warning(f"  Download timed out for {pdf_url}")
            return False

    source = Path(download_dir) / new_file
    try:
        # Move from Chrome's download dir to the target location
        shutil.move(str(source), str(save_path))
        file_size = save_path.stat().st_size
        if file_size < 500:
            log.warning(f"  Suspicious file size ({file_size} bytes)")
        else:
            log.info(f"  Saved: {save_path.name} ({file_size:,} bytes)")
        return True
    except Exception as e:
        log.error(f"  Failed to move downloaded file: {e}")
        return False


def sanitize_filename(name):
    """Create a safe directory name from a fund name."""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', '_', name)
    return name[:80]


def init_selenium_driver(download_dir):
    """Initialize Selenium Chrome driver with proper settings."""
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    os.makedirs(download_dir, exist_ok=True)

    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)

    # Configure Chrome to download PDFs instead of opening them
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
        "safebrowsing.enabled": True,
    }
    options.add_experimental_option("prefs", prefs)

    # Try webdriver_manager first, fall back to system chromedriver
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
    except Exception as e1:
        log.info(f"  webdriver_manager failed ({e1}), trying system Chrome...")
        driver = webdriver.Chrome(options=options)

    # Enable downloads in headless mode
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": download_dir
    })

    # Remove webdriver detection flags
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
    })

    return driver


def main():
    parser = argparse.ArgumentParser(description="Download Tadawul equity fund quarterly report PDFs")
    parser.add_argument('--test', action='store_true', help='Test mode: only process 3 funds')
    parser.add_argument('--list', action='store_true', help='Just list funds, no download')
    parser.add_argument('--fund', type=str, help='Only process funds matching this name (case-insensitive)')
    parser.add_argument('--output', type=str, default=str(PDF_DIR), help='Output directory for PDFs')
    parser.add_argument('--delay', type=float, default=2.0, help='Delay between requests (seconds)')
    parser.add_argument('--flat', action='store_true', help='Save PDFs flat (not in fund subfolders)')
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize Selenium (required — Tadawul blocks non-browser requests)
    selenium_download_dir = str(output_dir / "_chrome_downloads")
    try:
        driver = init_selenium_driver(selenium_download_dir)
        log.info(f"Selenium driver initialized (downloads -> {selenium_download_dir})")
    except ImportError:
        log.error("Selenium not installed. Run: pip install selenium webdriver-manager")
        sys.exit(1)
    except Exception as e:
        log.error(f"Failed to initialize Selenium: {e}")
        log.error("Make sure Chrome/Chromium is installed on your system.")
        sys.exit(1)

    # Step 1: Dynamically scrape fund list from Tadawul listing page
    all_equity_funds = scrape_fund_list(driver)

    if not all_equity_funds:
        log.error("No equity funds found on listing page. Exiting.")
        driver.quit()
        sys.exit(1)

    # Filter funds
    funds = all_equity_funds[:]
    if args.fund:
        funds = [f for f in funds if args.fund.lower() in f[0].lower()]
        log.info(f"Filtered to {len(funds)} funds matching '{args.fund}'")
    if args.test:
        funds = funds[:3]
        log.info("TEST MODE: Processing only 3 funds")

    # List mode
    if args.list:
        print(f"\n{'='*80}")
        print(f"  Tadawul Equity Funds ({len(funds)} total)")
        print(f"{'='*80}\n")
        for i, (name, manager, url) in enumerate(funds, 1):
            print(f"  {i:3d}. {name:<55s} {manager:<25s}")
        print()
        driver.quit()
        return

    print(f"\n{'='*70}")
    print(f"  Tadawul Equity Fund PDF Downloader")
    print(f"  Processing {len(funds)} funds (URLs scraped live from Tadawul)")
    print(f"  Output: {output_dir}")
    print(f"{'='*70}\n")

    # Track results
    results = {
        "timestamp": datetime.now().isoformat(),
        "total_funds": len(funds),
        "funds_processed": 0,
        "pdfs_found": 0,
        "pdfs_downloaded": 0,
        "pdfs_skipped": 0,
        "errors": [],
        "details": []
    }

    total_pdfs = 0
    total_downloaded = 0
    total_skipped = 0

    for i, (name, manager, fund_url) in enumerate(funds, 1):
        log.info(f"[{i}/{len(funds)}] {name} ({manager})")

        # Fetch the fund's profile page using the URL scraped from the listing
        html = fetch_fund_page_selenium(driver, fund_url, name)

        fund_detail = {
            "name": name,
            "manager": manager,
            "factsheets": [],
            "error": None
        }

        if not html:
            fund_detail["error"] = "Failed to fetch page"
            results["errors"].append(f"{name}: Failed to fetch page")
            results["details"].append(fund_detail)
            if args.delay:
                time.sleep(args.delay)
            continue

        # Extract PDF links
        factsheets, announcements = extract_pdf_links(html)
        all_pdfs = factsheets  # Prefer factsheet PDFs

        if not all_pdfs and announcements:
            all_pdfs = announcements
            log.info(f"  No factsheets found, using {len(announcements)} announcement PDFs")

        if not all_pdfs:
            log.warning(f"  No PDF links found for {name}")
            fund_detail["error"] = "No PDFs found on page"
            results["errors"].append(f"{name}: No PDFs found")
            results["details"].append(fund_detail)
            if args.delay:
                time.sleep(args.delay)
            continue

        log.info(f"  Found {len(all_pdfs)} factsheet PDFs")
        total_pdfs += len(all_pdfs)

        # Create fund subfolder (unless flat mode)
        if args.flat:
            fund_dir = output_dir
        else:
            fund_dir = output_dir / sanitize_filename(name)
            fund_dir.mkdir(parents=True, exist_ok=True)

        # Download each PDF
        for pdf_path in all_pdfs:
            filename = pdf_path.split('/')[-1]
            quarter = pdf_to_quarter(pdf_path)
            save_path = fund_dir / filename

            if save_path.exists():
                log.info(f"  SKIP (exists): {filename} [{quarter}]")
                total_skipped += 1
                fund_detail["factsheets"].append({
                    "file": filename,
                    "quarter": quarter,
                    "status": "skipped"
                })
                continue

            pdf_url = f"{BASE_URL}{pdf_path}"
            log.info(f"  Downloading: {filename} [{quarter}]")

            ok = download_pdf_selenium(driver, pdf_url, save_path, selenium_download_dir)
            if ok:
                total_downloaded += 1
                fund_detail["factsheets"].append({
                    "file": filename,
                    "quarter": quarter,
                    "status": "downloaded"
                })
            else:
                fund_detail["factsheets"].append({
                    "file": filename,
                    "quarter": quarter,
                    "status": "failed"
                })

        results["details"].append(fund_detail)
        results["funds_processed"] += 1

        # Rate limiting
        if args.delay and i < len(funds):
            time.sleep(args.delay)

    # Cleanup Selenium
    if driver:
        driver.quit()

    # Clean up temp download directory
    if selenium_download_dir and os.path.exists(selenium_download_dir):
        try:
            remaining = os.listdir(selenium_download_dir)
            if not remaining:
                os.rmdir(selenium_download_dir)
            else:
                log.info(f"  Note: {len(remaining)} leftover files in {selenium_download_dir}")
        except Exception:
            pass

    # Save results
    results["pdfs_found"] = total_pdfs
    results["pdfs_downloaded"] = total_downloaded
    results["pdfs_skipped"] = total_skipped

    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Summary
    print(f"\n{'='*70}")
    print(f"  Download Complete!")
    print(f"{'='*70}")
    print(f"  Funds processed:  {results['funds_processed']}/{len(funds)}")
    print(f"  PDFs found:       {total_pdfs}")
    print(f"  PDFs downloaded:  {total_downloaded}")
    print(f"  PDFs skipped:     {total_skipped} (already existed)")
    print(f"  Errors:           {len(results['errors'])}")
    print(f"  Log saved to:     {LOG_FILE}")
    print(f"  PDFs saved to:    {output_dir}")
    print(f"{'='*70}\n")

    if results['errors']:
        print("  Errors:")
        for err in results['errors'][:10]:
            print(f"    - {err}")
        if len(results['errors']) > 10:
            print(f"    ... and {len(results['errors']) - 10} more (see log file)")
        print()

    # Hint about next steps
    if total_downloaded > 0 or total_skipped > 0:
        print("  NEXT STEPS:")
        print("  1. Move/copy the downloaded PDFs to this folder")
        print("  2. Run: python scraper.py --local")
        print("     (or: python scraper.py --local --use-ai  for best results)")
        print("  3. Open index.html to see your updated dashboard")
        print()


if __name__ == "__main__":
    main()
