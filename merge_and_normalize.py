#!/usr/bin/env python3
"""
Merge extracted data from results_parts/ and normalize all company names
using canonical_names.json + tadawul_master.json.

Usage:
  python merge_and_normalize.py                          # All quarters
  python merge_and_normalize.py --quarters Q4-2025 Q1-2026  # Specific quarters only
  python merge_and_normalize.py --all                    # All quarters (explicit)
"""

import json, re, os, glob, argparse
from collections import Counter

parser = argparse.ArgumentParser()
parser.add_argument("--quarters", nargs="*", help="Only include these quarters")
parser.add_argument("--all", action="store_true", help="Include all quarters")
args = parser.parse_args()

RESULTS_DIR = "results_parts"
CANONICAL_FILE = "canonical_names.json"
TADAWUL_FILE = "tadawul_master.json"

# ─── Load canonical names ─────────────────────────────────────────
if os.path.exists(CANONICAL_FILE):
    canonical = json.load(open(CANONICAL_FILE, 'r', encoding='utf-8'))
    print(f"Loaded {len(canonical)} canonical name mappings")
else:
    canonical = {}
    print("WARNING: canonical_names.json not found")

# ─── Load Tadawul master ──────────────────────────────────────────
alias_map = {}
if os.path.exists(TADAWUL_FILE):
    tadawul = json.load(open(TADAWUL_FILE, 'r', encoding='utf-8'))
    for co in tadawul['companies']:
        name = co['name_en']
        for alias in co.get('aliases', []):
            alias_map[alias.lower().strip()] = name
        alias_map[name.lower().strip()] = name
        if co.get('name_ar'):
            alias_map[co['name_ar'].lower().strip()] = name
    print(f"Loaded {len(alias_map)} Tadawul aliases")

# ─── Garbage filter ───────────────────────────────────────────────
def is_garbage(name):
    if not name or len(name.strip()) < 1:
        return True
    n = name.strip()
    arabic_kw = ['صندوق','استثمار','الأسهم','السعودي','بنك','شركة','المالية','الربع',
                 'الأول','الثاني','الثالث','الرابع','البند','أداء','املؤشر','الحالي',
                 'االسترشادي','المرجعي','يناير','فبراير','مارس','أبريل','مايو','يونيو',
                 'يوليو','أغسطس','سبتمبر','أكتوبر','نوفمبر','ديسمبر']
    if any(k in n for k in arabic_kw):
        return True
    low = n.lower()
    eng_garbage = ['benchmark','index','total','n/a','none','portfolio',
                   'asset','quarter','period','date','month','january','february','march',
                   'april','may','june','july','august','september','october','november',
                   'december','q1','q2','q3','q4','year','annual','return','nav',
                   'top 10','top ten','weight','sector']
    if any(k in low for k in eng_garbage):
        return True
    # "cash" and "other" only when they ARE the name, not part of a company name
    if low in ('cash', 'other', 'others', 'cash & cash equivalent', 'cash & cash equivalents',
               'cash and cash equivalents', 'sar principal cash'):
        return True
    # Fixed-income instruments are not equity stocks
    if any(k in low for k in ['sukuk', 'murabaha', 'murabaha deposit', 'tier 1', 'ucits',
                               'ishares', 'pimco', 'threadneedle', 'nomura fds', 'gmo quality',
                               'liquidity fund']):
        return True
    # Allow listed REITs (e.g. "Jadwa REIT Saudi Fund", "Bonyan REIT Fund")
    reit_whitelist = ['jadwa reit', 'bonyan reit', 'aljazira reit', 'alahli reit',
                      'derayah reit', 'musharaka reit', 'mulkia reit', 'sedco reit',
                      'riyad reit', 'alinma retail reit', 'swicorp reit']
    if not any(w in low for w in reit_whitelist):
        if any(k in low for k in ['fund','etf','reit']):
            return True
    if re.match(r'^[\d\s.,%/\-+()]+$', n):
        return True
    if '@' in n or len(n) > 120:
        return True
    return False

# ─── Build case-insensitive canonical lookup ─────────────────────
canonical_lower = {}
for k, v in canonical.items():
    canonical_lower[k.lower().strip()] = v

# ─── Normalize name ───────────────────────────────────────────────
def normalize(name):
    if not name:
        return None
    n = name.strip()
    if is_garbage(n):
        return None
    # 1. Check canonical names (exact match first, then case-insensitive)
    if n in canonical:
        result = canonical[n]
        if result is None:
            return None  # Explicitly marked as garbage
        return result
    low = n.lower().strip()
    if low in canonical_lower:
        result = canonical_lower[low]
        if result is None:
            return None
        return result
    # 2. Check Tadawul aliases (case-insensitive)
    if low in alias_map:
        tadawul_name = alias_map[low]
        # Re-check canonical for the Tadawul result (it may need normalization too)
        if tadawul_name in canonical:
            result = canonical[tadawul_name]
            if result is None:
                return None
            return result
        return tadawul_name
    # 3. Return as-is
    return n

# ─── Normalize fund key ──────────────────────────────────────────
def normalize_fund_key(fk):
    """Normalize fund key: lowercase, replace dashes with underscores, collapse multiples."""
    fk = fk.lower().strip()
    fk = fk.replace('-', '_')
    # Collapse triple+ underscores to double (for "- Class" patterns)
    while '____' in fk:
        fk = fk.replace('____', '___')
    return fk

# ─── Load parts and build output ──────────────────────────────────
target_quarters = set(args.quarters) if args.quarters else None

output = {'funds': {}}
loaded = 0
skipped = 0

# Prefer _hires parts over standard
parts = {}
for pf in sorted(glob.glob(os.path.join(RESULTS_DIR, '*.json'))):
    basename = os.path.basename(pf)
    is_hires = '_hires.json' in basename

    try:
        part = json.load(open(pf, 'r', encoding='utf-8'))
    except:
        continue

    fk = normalize_fund_key(part.get('fund_key', ''))
    q = part.get('quarter', '')
    if not fk or not q:
        continue
    if target_quarters and q not in target_quarters:
        continue

    key = (fk, q)
    if key not in parts or is_hires:
        parts[key] = part

# Process all parts
for (fk, q), part in parts.items():
    holdings = []
    for h in part.get('holdings', []):
        name = normalize(h.get('name', ''))
        if name:
            entry = {
                'rank': h.get('rank', 0),
                'name': name,
                'ticker': h.get('ticker', ''),
                'weight': h.get('weight', h.get('weight_percent', 0))
            }
            if h.get('estimated'):
                entry['estimated'] = True
            holdings.append(entry)

    if holdings:
        # fk is already normalized via normalize_fund_key
        if fk not in output['funds']:
            output['funds'][fk] = {'id': fk, 'name': part['fund'], 'quarters': {}}
        output['funds'][fk]['quarters'][q] = {
            'period': q.replace('-', ' '),
            'source': part.get('method', 'claude_code'),
            'holdings': holdings
        }
        loaded += 1
    else:
        skipped += 1

# Remove empty funds
output['funds'] = {k: v for k, v in output['funds'].items() if v.get('quarters')}

# ─── Stats ────────────────────────────────────────────────────────
funds = len(output['funds'])
quarters = sum(len(f['quarters']) for f in output['funds'].values())
qc = Counter()
stocks = set()
for fd in output['funds'].values():
    for q, qd in fd['quarters'].items():
        qc[q] += 1
        for h in qd['holdings']:
            stocks.add(h['name'])

print(f"\n{'='*60}")
print(f"  Funds: {funds}")
print(f"  Quarters: {quarters} ({loaded} loaded, {skipped} empty)")
print(f"  Unique stocks: {len(stocks)}")
print(f"  By quarter:")
for q in sorted(qc):
    print(f"    {q}: {qc[q]} funds")
print(f"{'='*60}")

# ─── Save ─────────────────────────────────────────────────────────
with open('fund_data_merged.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

with open('fund_data.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

js = 'window.__FUND_DATA__=' + json.dumps(output, ensure_ascii=False, separators=(',',':')) + ';'
with open('fund_data.min.js', 'w', encoding='utf-8') as f:
    f.write(js)

print(f"\nSaved: fund_data_merged.json, fund_data.json, fund_data.min.js ({len(js)//1024} KB)")
