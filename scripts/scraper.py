#!/usr/bin/env python3
"""
Tadawul Fund Report Scraper & Extractor
========================================
Automatically scrapes Saudi Exchange (Tadawul) for all public equity fund
quarterly reports, downloads PDFs, extracts top 10 holdings, and updates
the fund_data.json dashboard file.

QUICK START (Local PDFs):
    python scraper.py --local              # Process all PDFs in this folder
    python scraper.py --local --use-ai     # Use Claude API for better extraction

FULL PIPELINE (Selenium):
    python scraper.py                      # Full run: scrape + extract + generate
    python scraper.py --list-funds         # Just list all equity funds
    python scraper.py --download-only      # Only download PDFs (no extraction)
    python scraper.py --extract-only       # Only extract from already-downloaded PDFs
    python scraper.py --quarter Q4-2025    # Target a specific quarter

Requirements:
    pip install pdfplumber requests

Optional:
    pip install selenium webdriver-manager   # For Tadawul scraping
    pip install anthropic                    # For AI-powered extraction (most reliable)
"""

import os
import sys
import json
import time
import re
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_URL = "https://www.saudiexchange.sa"
MUTUAL_FUNDS_URL = f"{BASE_URL}/wps/portal/saudiexchange/ourmarkets/funds-market-watch/mutual-funds"
PDF_BASE_URL = f"{BASE_URL}/Resources/fsPdf"

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
PDF_DIR = DATA_DIR / "pdfs"
OUTPUT_FILE = SCRIPT_DIR / "fund_data.json"
FUND_REGISTRY_FILE = DATA_DIR / "fund_registry.json"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("TadawulScraper")

# ---------------------------------------------------------------------------
# Fund ID -> Fund Key mapping (from fund_data.json)
# Maps Tadawul fundId codes to the keys used in our dashboard JSON
# ---------------------------------------------------------------------------
FUND_ID_MAP = {
    "321":   "riyad_blue_chip_equity_fund",
    "001027": "riyad_blue_chip_equity_fund",
    "349":   "alawwal_saudi_equity_fund",
    "000349": "alawwal_saudi_equity_fund",
    "3023":  "miyar_saudi_equity_fund",
    "003023": "miyar_saudi_equity_fund",
    "17912": "miyar_saudi_equity_fund",       # newer format
    "6206":  "winveston_saudi_equity_quant_fund",
    "006206": "winveston_saudi_equity_quant_fund",
    "331":   "yaqeen_saudi_equity_fund",
    "000331": "yaqeen_saudi_equity_fund",
    "340":   "morgan_stanley_saudi_equity_fund",
    "000340": "morgan_stanley_saudi_equity_fund",
    "350":   "snb_capital_gcc_growth_and_income_fund",
    "000350": "snb_capital_gcc_growth_and_income_fund",
    "000323": "riyad_leading_stocks_fund",
    "323":   "riyad_leading_stocks_fund",
}

# ---------------------------------------------------------------------------
# Known Saudi stock name mappings (Arabic + English -> Standard Name + Ticker)
# ---------------------------------------------------------------------------
STOCK_MAPPINGS = {
    # Al Rajhi Bank
    "مصرف الراجحي": {"en": "Al Rajhi Bank", "ticker": "1120"},
    "الراجحي": {"en": "Al Rajhi Bank", "ticker": "1120"},
    "AL RAJHI": {"en": "Al Rajhi Bank", "ticker": "1120"},
    "AL RAJHI BANK": {"en": "Al Rajhi Bank", "ticker": "1120"},
    "ALRAJHI": {"en": "Al Rajhi Bank", "ticker": "1120"},
    "RAJHI": {"en": "Al Rajhi Bank", "ticker": "1120"},
    # Saudi Aramco
    "أرامكو السعودية": {"en": "Saudi Aramco", "ticker": "2222"},
    "أرامكو": {"en": "Saudi Aramco", "ticker": "2222"},
    "SAUDI ARAMCO": {"en": "Saudi Aramco", "ticker": "2222"},
    "SAUDI ARABIAN OIL": {"en": "Saudi Aramco", "ticker": "2222"},
    "ARAMCO": {"en": "Saudi Aramco", "ticker": "2222"},
    # SNB
    "البنك الأهلي السعودي": {"en": "Saudi National Bank (SNB)", "ticker": "1180"},
    "الأهلي السعودي": {"en": "Saudi National Bank (SNB)", "ticker": "1180"},
    "SNB": {"en": "Saudi National Bank (SNB)", "ticker": "1180"},
    "SAUDI NATIONAL BANK": {"en": "Saudi National Bank (SNB)", "ticker": "1180"},
    "THE NATIONAL COMMERCIAL BANK": {"en": "Saudi National Bank (SNB)", "ticker": "1180"},
    # Bank Albilad
    "بنك البلاد": {"en": "Bank Albilad", "ticker": "1140"},
    "البلاد": {"en": "Bank Albilad", "ticker": "1140"},
    "ALBILAD": {"en": "Bank Albilad", "ticker": "1140"},
    "BANK ALBILAD": {"en": "Bank Albilad", "ticker": "1140"},
    # Alinma Bank
    "مصرف الإنماء": {"en": "Alinma Bank", "ticker": "1150"},
    "الإنماء": {"en": "Alinma Bank", "ticker": "1150"},
    "ALINMA": {"en": "Alinma Bank", "ticker": "1150"},
    "ALINMA BANK": {"en": "Alinma Bank", "ticker": "1150"},
    # SABIC
    "سابك": {"en": "SABIC", "ticker": "2010"},
    "SABIC": {"en": "SABIC", "ticker": "2010"},
    # STC
    "الاتصالات السعودية": {"en": "STC (Saudi Telecom)", "ticker": "7010"},
    "STC": {"en": "STC (Saudi Telecom)", "ticker": "7010"},
    "SAUDI TELECOM": {"en": "STC (Saudi Telecom)", "ticker": "7010"},
    "SAUDI TELECOMMUNICATION": {"en": "STC (Saudi Telecom)", "ticker": "7010"},
    # Riyad Bank
    "بنك الرياض": {"en": "Riyad Bank", "ticker": "1010"},
    "RIYAD BANK": {"en": "Riyad Bank", "ticker": "1010"},
    "RIYAD": {"en": "Riyad Bank", "ticker": "1010"},
    # SAB
    "البنك السعودي الأول": {"en": "Saudi Awwal Bank (SAB)", "ticker": "1060"},
    "SAB": {"en": "Saudi Awwal Bank (SAB)", "ticker": "1060"},
    "SAUDI AWWAL BANK": {"en": "Saudi Awwal Bank (SAB)", "ticker": "1060"},
    "SAUDI BRITISH BANK": {"en": "Saudi Awwal Bank (SAB)", "ticker": "1060"},
    # Tawuniya
    "التعاونية": {"en": "Tawuniya", "ticker": "8010"},
    "TAWUNIYA": {"en": "Tawuniya", "ticker": "8010"},
    # Mouwasat
    "المواساة": {"en": "Mouwasat Medical", "ticker": "4002"},
    "MOUWASAT": {"en": "Mouwasat Medical", "ticker": "4002"},
    # Mobily
    "اتحاد اتصالات": {"en": "Etihad Etisalat (Mobily)", "ticker": "7020"},
    "MOBILY": {"en": "Etihad Etisalat (Mobily)", "ticker": "7020"},
    "ETIHAD ETISALAT": {"en": "Etihad Etisalat (Mobily)", "ticker": "7020"},
    # Ma'aden
    "معادن": {"en": "Ma'aden", "ticker": "1211"},
    "MAADEN": {"en": "Ma'aden", "ticker": "1211"},
    "MA'ADEN": {"en": "Ma'aden", "ticker": "1211"},
    # Saudi Cement
    "أسمنت السعودية": {"en": "Saudi Cement", "ticker": "3030"},
    "SAUDI CEMENT": {"en": "Saudi Cement", "ticker": "3030"},
    # Dar Al Arkan
    "دار الأركان": {"en": "Dar Al Arkan", "ticker": "4300"},
    "DAR AL ARKAN": {"en": "Dar Al Arkan", "ticker": "4300"},
    # Jarir
    "جرير": {"en": "Jarir Marketing", "ticker": "4190"},
    "JARIR": {"en": "Jarir Marketing", "ticker": "4190"},
    # Almarai
    "المراعي": {"en": "Almarai", "ticker": "2280"},
    "ALMARAI": {"en": "Almarai", "ticker": "2280"},
    # Al Drees
    "الدريس": {"en": "Al Drees Petroleum", "ticker": "4200"},
    "AL DREES": {"en": "Al Drees Petroleum", "ticker": "4200"},
    # Seera Group
    "مجموعة سيرا": {"en": "Seera Group", "ticker": "1810"},
    "SEERA": {"en": "Seera Group", "ticker": "1810"},
    "SEERA GROUP": {"en": "Seera Group", "ticker": "1810"},
    # Flynas
    "طيران ناس": {"en": "Flynas", "ticker": "1820"},
    "FLYNAS": {"en": "Flynas", "ticker": "1820"},
    # Saudi Electricity
    "الكهرباء السعودية": {"en": "Saudi Electricity", "ticker": "5110"},
    "SEC": {"en": "Saudi Electricity", "ticker": "5110"},
    "SAUDI ELECTRICITY": {"en": "Saudi Electricity", "ticker": "5110"},
    # Rasan
    "رسن": {"en": "Rasan", "ticker": "3034"},
    "RASAN": {"en": "Rasan", "ticker": "3034"},
    # Cenomi Retail
    "سينومي ريتيل": {"en": "Cenomi Retail", "ticker": "4003"},
    "CENOMI RETAIL": {"en": "Cenomi Retail", "ticker": "4003"},
    "CENOMI": {"en": "Cenomi Retail", "ticker": "4003"},
    # Ataa Educational
    "عطاء التعليمية": {"en": "Ataa Educational", "ticker": "4292"},
    "ATAA": {"en": "Ataa Educational", "ticker": "4292"},
    "ATAA EDUCATIONAL": {"en": "Ataa Educational", "ticker": "4292"},
    # Astra Industrial
    "مجموعة أسترا": {"en": "Astra Industrial", "ticker": "1212"},
    "ASTRA": {"en": "Astra Industrial", "ticker": "1212"},
    "ASTRA INDUSTRIAL": {"en": "Astra Industrial", "ticker": "1212"},
    # SABB
    "البنك السعودي البريطاني": {"en": "SABB", "ticker": "1050"},
    "SABB": {"en": "SABB", "ticker": "1050"},
    # Arab National Bank
    "البنك العربي الوطني": {"en": "Arab National Bank", "ticker": "1080"},
    "ANB": {"en": "Arab National Bank", "ticker": "1080"},
    "ARAB NATIONAL BANK": {"en": "Arab National Bank", "ticker": "1080"},
    # Bupa Arabia
    "بوبا العربية": {"en": "Bupa Arabia", "ticker": "8210"},
    "BUPA": {"en": "Bupa Arabia", "ticker": "8210"},
    "BUPA ARABIA": {"en": "Bupa Arabia", "ticker": "8210"},
    # ACWA Power
    "أكوا باور": {"en": "ACWA Power", "ticker": "2082"},
    "ACWA POWER": {"en": "ACWA Power", "ticker": "2082"},
    "ACWA": {"en": "ACWA Power", "ticker": "2082"},
    # Bahri
    "الشركة الوطنية السعودية للنقل البحري": {"en": "Bahri", "ticker": "4030"},
    "BAHRI": {"en": "Bahri", "ticker": "4030"},
    # Emaar
    "إعمار المدينة": {"en": "Emaar Economic City", "ticker": "4220"},
    "EMAAR": {"en": "Emaar Economic City", "ticker": "4220"},
    # Extra stocks commonly found in fund reports
    "STCPAY": {"en": "stc pay", "ticker": "4291"},
    "STC PAY": {"en": "stc pay", "ticker": "4291"},
    "LEEJAM SPORTS": {"en": "Leejam Sports", "ticker": "1830"},
    "LEEJAM": {"en": "Leejam Sports", "ticker": "1830"},
    "لجام": {"en": "Leejam Sports", "ticker": "1830"},
    "ALDAWAA MEDICAL": {"en": "Al Dawaa Medical Services", "ticker": "4163"},
    "ALDAWAA": {"en": "Al Dawaa Medical Services", "ticker": "4163"},
    "الدواء": {"en": "Al Dawaa Medical Services", "ticker": "4163"},
    "EXTRA": {"en": "United Electronics (EXTRA)", "ticker": "4003"},
    "BIN DAWOOD": {"en": "BinDawood Holding", "ticker": "4161"},
    "BINDAWOOD": {"en": "BinDawood Holding", "ticker": "4161"},
    "بن داود": {"en": "BinDawood Holding", "ticker": "4161"},
    "NAHDI MEDICAL": {"en": "Nahdi Medical", "ticker": "4164"},
    "NAHDI": {"en": "Nahdi Medical", "ticker": "4164"},
    "النهدي": {"en": "Nahdi Medical", "ticker": "4164"},
    "ELM": {"en": "Elm Company", "ticker": "7203"},
    "SAUDI KAYAN": {"en": "Saudi Kayan", "ticker": "2350"},
    "KAYAN": {"en": "Saudi Kayan", "ticker": "2350"},
    "BANQUE SAUDI FRANSI": {"en": "Banque Saudi Fransi", "ticker": "1050"},
    "BSF": {"en": "Banque Saudi Fransi", "ticker": "1050"},
    "RIYADH CABLES": {"en": "Riyadh Cables", "ticker": "4142"},
    "CITY CEMENT": {"en": "City Cement", "ticker": "3003"},
    "MIS": {"en": "Middle East Specialized Cables (MIS)", "ticker": "2370"},
    "SAUDI CHEMICAL": {"en": "Saudi Chemical Company", "ticker": "2230"},
    "YANBU CEMENT": {"en": "Yanbu Cement", "ticker": "3060"},
    "EASTERN CEMENT": {"en": "Eastern Cement", "ticker": "3080"},
    "SAVOLA": {"en": "Savola Group", "ticker": "2050"},
    "سافولا": {"en": "Savola Group", "ticker": "2050"},
    "SAUDI GROUND SERVICES": {"en": "Saudi Ground Services", "ticker": "4031"},
    "SGS": {"en": "Saudi Ground Services", "ticker": "4031"},
}


# ---------------------------------------------------------------------------
# PDF Filename Parser
# ---------------------------------------------------------------------------
def parse_pdf_filename(filename):
    """
    Parse Tadawul PDF filenames to extract fund ID, date, and language.

    Formats:
      {fundId}_{type}_{date}_{time}_{lang}.pdf    e.g. 321_5_2025-10-14_15-00-01_En.pdf
      {extra}_{fundId}_{date}_{time}_{lang}.pdf   e.g. 17912_3023_2025-01-14_16-52-27_en.pdf

    Returns dict with: fund_id, date, lang, quarter
    """
    stem = Path(filename).stem
    parts = stem.split("_")

    if len(parts) < 4:
        return None

    # Try to find a date part (YYYY-MM-DD format)
    date_str = None
    date_idx = None
    for i, p in enumerate(parts):
        if re.match(r'^\d{4}-\d{2}-\d{2}$', p):
            date_str = p
            date_idx = i
            break

    if not date_str or not date_idx:
        return None

    # Language is usually the last part
    lang = parts[-1].lower() if parts[-1].lower() in ("en", "ar") else "unknown"

    # Fund ID: figure out which part(s) before the date represent the fund
    # Pattern 1: fundId_type_date (e.g., 321_5_2025-...)
    # Pattern 2: prefix_fundId_date (e.g., 17912_3023_2025-...)
    pre_date_parts = parts[:date_idx]

    # The fund ID is the one that maps to a known fund
    fund_id = None
    for p in pre_date_parts:
        if p in FUND_ID_MAP:
            fund_id = p
            break

    # If none found, try the first numeric part
    if not fund_id:
        for p in pre_date_parts:
            if p.isdigit():
                fund_id = p
                break

    # Map date to quarter
    # The PDF publish date is ~2 weeks after quarter end
    # Q1 ends Mar 31 -> PDFs appear ~Apr 10-15
    # Q2 ends Jun 30 -> PDFs appear ~Jul 10-15
    # Q3 ends Sep 30 -> PDFs appear ~Oct 10-15
    # Q4 ends Dec 31 -> PDFs appear ~Jan 10-15 (next year)
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d")
        month = date.month
        year = date.year

        if month in (1, 2, 3):
            # Jan-Mar publish -> Q4 of previous year
            quarter = f"Q4-{year - 1}"
        elif month in (4, 5, 6):
            # Apr-Jun publish -> Q1 of same year
            quarter = f"Q1-{year}"
        elif month in (7, 8, 9):
            # Jul-Sep publish -> Q2 of same year
            quarter = f"Q2-{year}"
        elif month in (10, 11, 12):
            # Oct-Dec publish -> Q3 of same year
            quarter = f"Q3-{year}"
    except ValueError:
        quarter = None

    return {
        "fund_id": fund_id,
        "date": date_str,
        "lang": lang,
        "quarter": quarter,
        "filename": filename
    }


def date_to_quarter_end(quarter):
    """Convert quarter string like Q4-2025 to end date like 2025-12-31."""
    q, year = quarter.split("-")
    ends = {"Q1": f"{year}-03-31", "Q2": f"{year}-06-30", "Q3": f"{year}-09-30", "Q4": f"{year}-12-31"}
    return ends.get(q, "")


# ---------------------------------------------------------------------------
# Holdings Extraction from PDFs
# ---------------------------------------------------------------------------
def extract_holdings_from_pdf(pdf_path, use_ai=False):
    """
    Extract top 10 holdings from a fund quarterly report PDF.

    Tries multiple strategies in order:
    1. Claude API (if --use-ai enabled) - most reliable
    2. Stock name matching with nearby percentages
    3. Table parsing
    4. Regex-based extraction
    5. Word-position extraction (for chart-based PDFs)
    """
    # Strategy 0: Claude API (most reliable for all formats)
    if use_ai:
        holdings = extract_with_claude_api(pdf_path)
        if holdings and len(holdings) >= 3:
            return holdings

    try:
        import pdfplumber
    except ImportError:
        log.error("pdfplumber not installed. Run: pip install pdfplumber")
        return []

    holdings = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = ""
            tables_data = []
            all_words = []

            for page in pdf.pages:
                text = page.extract_text() or ""
                full_text += text + "\n"

                # Extract tables
                tables = page.extract_tables()
                for table in tables:
                    tables_data.extend(table)

                # Extract individual words with positions (for chart reading)
                try:
                    words = page.extract_words()
                    all_words.extend([(w["text"], w.get("x0", 0), w.get("top", 0), w.get("x1", 0), w.get("bottom", 0)) for w in words])
                except:
                    pass

            # Strategy 1: Known stock name matching
            holdings = extract_by_stock_matching(full_text, tables_data)
            if len(holdings) >= 5:
                log.info(f"  Strategy 1 (stock matching): {len(holdings)} holdings")
                return holdings[:10]

            # Strategy 2: Table parsing
            holdings2 = extract_from_tables(tables_data)
            if len(holdings2) > len(holdings):
                holdings = holdings2
            if len(holdings) >= 5:
                log.info(f"  Strategy 2 (table parsing): {len(holdings)} holdings")
                return holdings[:10]

            # Strategy 3: Regex-based
            holdings3 = extract_by_regex(full_text)
            if len(holdings3) > len(holdings):
                holdings = holdings3
            if len(holdings) >= 5:
                log.info(f"  Strategy 3 (regex): {len(holdings)} holdings")
                return holdings[:10]

            # Strategy 4: Word-position extraction for chart labels
            holdings4 = extract_from_chart_words(all_words, full_text)
            if len(holdings4) > len(holdings):
                holdings = holdings4
            if len(holdings) >= 3:
                log.info(f"  Strategy 4 (chart words): {len(holdings)} holdings")
                return holdings[:10]

    except Exception as e:
        log.warning(f"Error extracting from {pdf_path}: {e}")

    if holdings:
        log.info(f"  Best extraction: {len(holdings)} holdings")
    else:
        log.warning(f"  No holdings extracted")

    return holdings[:10]


def extract_by_stock_matching(text, tables):
    """Match known stock names in the text and associate with nearby percentages."""
    found = {}

    # Combine text and table content
    all_text = text
    for row in tables:
        if row:
            all_text += " ".join(str(cell) for cell in row if cell) + "\n"

    for stock_name, info in STOCK_MAPPINGS.items():
        if stock_name.lower() in all_text.lower():
            # Find percentage near this stock name
            pattern = re.escape(stock_name) + r'[^%\n]{0,60}?([\d]+\.[\d]+)\s*%?'
            match = re.search(pattern, all_text, re.IGNORECASE)
            if match:
                weight = float(match.group(1))
                if 0.5 < weight < 30:
                    en_name = info["en"]
                    if en_name not in found or weight > found[en_name]["weight"]:
                        found[en_name] = {
                            "name": en_name,
                            "nameAr": stock_name if any('\u0600' <= c <= '\u06FF' for c in stock_name) else "",
                            "ticker": info["ticker"],
                            "weight": weight
                        }

    results = sorted(found.values(), key=lambda x: x["weight"], reverse=True)
    return [{"rank": i+1, **h} for i, h in enumerate(results[:10])]


def extract_from_tables(tables):
    """Extract holdings from parsed table data."""
    holdings = []

    for row in tables:
        if not row or len(row) < 2:
            continue

        row_text = " ".join(str(cell) for cell in row if cell)
        pct_match = re.search(r'([\d]+\.[\d]+)\s*%?', row_text)

        if pct_match:
            weight = float(pct_match.group(1))
            if 1 < weight < 30:
                for stock_name, info in STOCK_MAPPINGS.items():
                    if stock_name.lower() in row_text.lower():
                        holdings.append({
                            "name": info["en"],
                            "nameAr": stock_name if any('\u0600' <= c <= '\u06FF' for c in stock_name) else "",
                            "ticker": info["ticker"],
                            "weight": weight
                        })
                        break

    # Deduplicate and sort
    seen = set()
    unique = []
    for h in sorted(holdings, key=lambda x: x["weight"], reverse=True):
        if h["name"] not in seen:
            seen.add(h["name"])
            unique.append(h)

    return [{"rank": i+1, **h} for i, h in enumerate(unique[:10])]


def extract_by_regex(text):
    """Regex-based extraction for English reports with tabular format."""
    holdings = []

    # Pattern: "1  STOCK NAME  XX.XX%"
    pattern = r'(\d+)\s+([A-Z][A-Z\s&\'.()-]+?)\s+([\d]+\.[\d]+)\s*%'
    matches = re.findall(pattern, text)

    for rank, name, weight in matches:
        weight_f = float(weight)
        if 1 < weight_f < 30:
            name_clean = name.strip()
            matched = None
            for stock_name, info in STOCK_MAPPINGS.items():
                if stock_name.upper() in name_clean.upper() or name_clean.upper() in stock_name.upper():
                    matched = info
                    break

            holdings.append({
                "rank": int(rank),
                "name": matched["en"] if matched else name_clean,
                "nameAr": "",
                "ticker": matched["ticker"] if matched else "",
                "weight": weight_f
            })

    return sorted(holdings, key=lambda x: x["weight"], reverse=True)[:10]


def extract_from_chart_words(words, full_text):
    """
    Extract holdings from chart x-axis labels using word positions.
    Used for PDFs that show top 10 as a bar chart with rotated labels.
    """
    if not words:
        return []

    # Find percentage values in text
    pct_pattern = r'([\d]+\.[\d]+)\s*%'
    percentages = [float(m) for m in re.findall(pct_pattern, full_text)]
    percentages = [p for p in percentages if 1 < p < 30]

    if not percentages:
        return []

    # Try to match stock names from words near the bottom of charts
    # Chart labels are typically in the lower 30% of the page
    found_stocks = []
    for word_text, x0, top, x1, bottom in words:
        for stock_name, info in STOCK_MAPPINGS.items():
            if len(stock_name) >= 3 and stock_name.upper() in word_text.upper():
                if info["en"] not in [s["name"] for s in found_stocks]:
                    found_stocks.append({
                        "name": info["en"],
                        "ticker": info["ticker"],
                        "x_pos": x0
                    })

    if not found_stocks and percentages:
        # Return percentages with placeholder names
        holdings = []
        for i, pct in enumerate(sorted(percentages, reverse=True)[:10]):
            holdings.append({
                "rank": i + 1,
                "name": f"Holding {i+1}",
                "ticker": "",
                "weight": pct
            })
        return holdings

    # Sort stocks by x position (left to right) and pair with percentages
    found_stocks.sort(key=lambda s: s["x_pos"])
    percentages_sorted = sorted(percentages, reverse=True)

    holdings = []
    for i, stock in enumerate(found_stocks[:10]):
        weight = percentages_sorted[i] if i < len(percentages_sorted) else 0
        holdings.append({
            "rank": i + 1,
            "name": stock["name"],
            "ticker": stock["ticker"],
            "weight": weight
        })

    return sorted(holdings, key=lambda x: x["weight"], reverse=True)


def extract_with_claude_api(pdf_path):
    """
    Use Claude API to extract top 10 holdings from a PDF.
    Most reliable method - handles Arabic, charts, varying formats.

    Requires: ANTHROPIC_API_KEY environment variable
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.info("No ANTHROPIC_API_KEY set. Skipping AI extraction.")
        return None

    try:
        import anthropic
        import base64
    except ImportError:
        log.warning("anthropic package not installed. Run: pip install anthropic")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)

        with open(pdf_path, "rb") as f:
            pdf_data = base64.standard_b64encode(f.read()).decode("utf-8")

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": """Extract the top 10 investment holdings from this Saudi fund quarterly report.

Return ONLY a JSON array with exactly this format (no other text):
[
  {"rank": 1, "name": "Stock Name in English", "nameAr": "اسم السهم بالعربي", "ticker": "1120", "weight": 15.9},
  ...
]

Rules:
- "weight" is the percentage of portfolio (e.g., 15.9 means 15.9%)
- Use standard Tadawul ticker codes
- Translate Arabic stock names to English
- If the report shows a bar chart of top 10 investments, read the values from it
- Return exactly 10 holdings sorted by weight descending
- Only include Saudi-listed stocks (Tadawul main market)"""
                    }
                ]
            }]
        )

        text = response.content[0].text.strip()
        json_match = re.search(r'\[[\s\S]*\]', text)
        if json_match:
            holdings = json.loads(json_match.group())
            log.info(f"  AI extracted {len(holdings)} holdings")
            return holdings

    except Exception as e:
        log.warning(f"  AI extraction failed: {e}")

    return None


# ---------------------------------------------------------------------------
# Helper: Write fund_data.js for file:// protocol compatibility
# ---------------------------------------------------------------------------
def write_fund_data_js(data):
    """Write fund_data.js so the dashboard works when opened via file:// protocol."""
    js_file = SCRIPT_DIR / "fund_data.js"
    with open(js_file, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by scraper.py - do not edit manually\n")
        f.write("window.__FUND_DATA__ = ")
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write(";\n")
    log.info(f"Updated fund_data.js for browser compatibility")


# ---------------------------------------------------------------------------
# LOCAL PDF Processing Mode (Main workflow for the user)
# ---------------------------------------------------------------------------
def process_local_pdfs(pdf_folder, use_ai=False, target_quarter=None):
    """
    Process all PDF files in the given folder:
    1. Parse filenames to determine fund ID and quarter
    2. Extract holdings from each PDF
    3. Merge results into existing fund_data.json

    This is the primary workflow - drop PDFs into the folder and run:
        python scraper.py --local
    """
    pdf_folder = Path(pdf_folder)
    # Search recursively for PDFs in subfolders (e.g., pdfs/{fund_name}/*.pdf)
    pdf_files = list(pdf_folder.glob("**/*.pdf"))

    if not pdf_files:
        log.warning(f"No PDF files found in {pdf_folder}")
        return

    log.info(f"Found {len(pdf_files)} PDF files in {pdf_folder}")

    # Load existing fund_data.json
    existing_data = {}
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing_data = json.load(f)

    funds = existing_data.get("funds", {})

    # Parse and group PDFs
    parsed_pdfs = []
    skipped = 0
    for pdf_file in sorted(pdf_files):
        info = parse_pdf_filename(pdf_file.name)
        if info:
            info["full_path"] = str(pdf_file)
            # Store the parent folder name as the fund identity
            info["folder"] = pdf_file.parent.name

            # Filter by target quarter if specified
            if target_quarter and info["quarter"] != target_quarter:
                continue

            # Prefer English PDFs for extraction
            parsed_pdfs.append(info)
        else:
            skipped += 1
            log.warning(f"  Could not parse filename: {pdf_file.name}")

    if skipped:
        log.info(f"  Skipped {skipped} files with unrecognized names")

    # Group by FOLDER + quarter (not fund_id, since multiple funds share IDs)
    # This ensures each fund subfolder is treated as a separate fund
    groups = {}
    for p in parsed_pdfs:
        folder = p.get("folder", p["fund_id"])
        key = f"{folder}_{p['quarter']}"
        if key not in groups or p["lang"] == "en":
            groups[key] = p

    log.info(f"Processing {len(groups)} fund-quarter combinations...")

    new_extractions = 0
    updated_extractions = 0
    failed = 0

    for key, info in sorted(groups.items()):
        fund_id = info["fund_id"]
        quarter = info["quarter"]
        pdf_path = info["full_path"]
        folder_name = info.get("folder", "")

        # Always derive fund key from folder name (since fund IDs are shared across funds)
        if folder_name and folder_name not in ("pdfs", ".", "_chrome_downloads"):
            fund_key = folder_name.lower()
        else:
            # Fallback to FUND_ID_MAP for PDFs not in subfolders
            fund_key = FUND_ID_MAP.get(fund_id)
            if not fund_key:
                log.warning(f"  Unknown fund: {info['filename']} (no folder, ID {fund_id} not in map)")
                continue

        # Check if we already have data for this fund-quarter
        if fund_key in funds:
            existing_quarters = funds[fund_key].get("quarters", {})
            if quarter in existing_quarters:
                existing_holdings = existing_quarters[quarter].get("holdings", [])
                # Skip if we already have good data (5+ named holdings)
                named = [h for h in existing_holdings if not h.get("name", "").startswith("Holding ")]
                if len(named) >= 5:
                    log.info(f"  [{fund_key}] {quarter}: Already has {len(named)} holdings, skipping")
                    continue

        log.info(f"  [{fund_key}] {quarter}: Extracting from {info['filename']}...")

        holdings = extract_holdings_from_pdf(pdf_path, use_ai=use_ai)

        if holdings and len(holdings) >= 1:
            # Ensure fund exists in data
            if fund_key not in funds:
                log.warning(f"  Fund key '{fund_key}' not found in fund_data.json. Adding shell entry.")
                funds[fund_key] = {
                    "id": fund_key,
                    "name": fund_key.replace("_", " ").title(),
                    "fundName": fund_key.replace("_", " ").title(),
                    "manager": "Unknown",
                    "currency": "SAR",
                    "shariah": "-",
                    "objective": "Growth",
                    "fundId": fund_id,
                    "quarters": {}
                }

            # Add/update quarter data
            if "quarters" not in funds[fund_key]:
                funds[fund_key]["quarters"] = {}

            end_date = date_to_quarter_end(quarter)
            funds[fund_key]["quarters"][quarter] = {
                "period": quarter.replace("-", " "),
                "endDate": end_date,
                "holdings": holdings
            }

            if fund_key in existing_data.get("funds", {}) and quarter in existing_data["funds"].get(fund_key, {}).get("quarters", {}):
                updated_extractions += 1
            else:
                new_extractions += 1

            log.info(f"    -> {len(holdings)} holdings extracted")

            # INCREMENTAL SAVE: write after every successful extraction
            # so data isn't lost if the script crashes or API credits run out
            existing_data["funds"] = funds
            try:
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, indent=2, ensure_ascii=False)
                write_fund_data_js(existing_data)
            except Exception as save_err:
                log.warning(f"    Could not save intermediate results: {save_err}")

        else:
            failed += 1
            log.warning(f"    -> FAILED to extract holdings")

    # Update metadata
    funds_with_holdings = sum(
        1 for f in funds.values()
        if any(q.get("holdings") for q in f.get("quarters", {}).values())
    )
    total_quarters = sum(
        len([q for q in f.get("quarters", {}).values() if q.get("holdings")])
        for f in funds.values()
    )

    existing_data["funds"] = funds
    existing_data["metadata"] = existing_data.get("metadata", {})
    existing_data["metadata"]["generatedAt"] = datetime.now().isoformat()
    existing_data["metadata"]["fundsWithHoldings"] = funds_with_holdings
    existing_data["metadata"]["totalDataPoints"] = total_quarters

    # Save JSON
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, indent=2, ensure_ascii=False)

    # Also save as JS for file:// protocol compatibility
    write_fund_data_js(existing_data)

    log.info("=" * 60)
    log.info(f"LOCAL PDF PROCESSING COMPLETE")
    log.info(f"  New extractions:     {new_extractions}")
    log.info(f"  Updated extractions: {updated_extractions}")
    log.info(f"  Failed:              {failed}")
    log.info(f"  Total funds w/data:  {funds_with_holdings}")
    log.info(f"  Total data points:   {total_quarters}")
    log.info(f"  Output: {OUTPUT_FILE}")
    log.info("=" * 60)


# ---------------------------------------------------------------------------
# Step 1: Scrape Fund List from Tadawul (Selenium)
# ---------------------------------------------------------------------------
def scrape_fund_list():
    """Scrape the full list of mutual funds from Tadawul using Selenium."""
    log.info("=" * 60)
    log.info("STEP 1: Scraping fund list from Tadawul...")
    log.info("=" * 60)

    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        log.error("Selenium not installed. Run: pip install selenium webdriver-manager")
        log.info("Falling back to registry file if available...")
        return load_fund_registry()

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
    except Exception:
        try:
            options.binary_location = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
            driver = webdriver.Chrome(options=options)
        except Exception:
            options.binary_location = "/usr/bin/google-chrome"
            driver = webdriver.Chrome(options=options)

    funds = []
    try:
        url = f"{MUTUAL_FUNDS_URL}?locale=en"
        log.info(f"Loading: {url}")
        driver.get(url)

        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr"))
        )
        time.sleep(3)

        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        log.info(f"Found {len(rows)} funds in table")

        for row in rows:
            try:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) < 5:
                    continue

                link = row.find_element(By.TAG_NAME, "a")
                name = link.text.strip()
                href = link.get_attribute("href") or ""
                manager = cells[1].text.strip()
                currency = cells[2].text.strip()
                shariah = cells[3].text.strip()
                objective = cells[4].text.strip()

                fund_id_match = re.search(r'selectedFund/(\d+)', href)
                fund_id = fund_id_match.group(1) if fund_id_match else ""

                if objective in ("Growth", "Income and Growth") and currency == "SAR":
                    funds.append({
                        "name": name,
                        "manager": manager,
                        "currency": currency,
                        "shariah": shariah,
                        "objective": objective,
                        "fundId": fund_id,
                        "profileUrl": href,
                    })
            except Exception:
                continue

        log.info(f"Extracted {len(funds)} equity/growth funds (SAR)")

    finally:
        driver.quit()

    save_fund_registry(funds)
    return funds


def save_fund_registry(funds):
    """Save fund list to local registry file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(FUND_REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "lastUpdated": datetime.now().isoformat(),
            "totalFunds": len(funds),
            "funds": funds
        }, f, indent=2, ensure_ascii=False)
    log.info(f"Saved fund registry: {len(funds)} funds -> {FUND_REGISTRY_FILE}")


def load_fund_registry():
    """Load fund list from local registry file."""
    if not FUND_REGISTRY_FILE.exists():
        log.warning("No fund registry found. Run with Selenium first.")
        return []
    with open(FUND_REGISTRY_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    log.info(f"Loaded fund registry: {data['totalFunds']} funds (updated {data['lastUpdated']})")
    return data["funds"]


# ---------------------------------------------------------------------------
# Step 2: Download Quarterly Report PDFs (Selenium)
# ---------------------------------------------------------------------------
def download_fund_reports(funds, quarter="Q4-2025"):
    """Download quarterly report PDFs from Tadawul fund profile pages."""
    log.info("=" * 60)
    log.info(f"STEP 2: Downloading {quarter} reports for {len(funds)} funds...")
    log.info("=" * 60)

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    quarter_dir = PDF_DIR / quarter
    quarter_dir.mkdir(exist_ok=True)

    quarter_dates = {
        "Q1": ("03-31", "01-01", "03-31"),
        "Q2": ("06-30", "04-01", "06-30"),
        "Q3": ("09-30", "07-01", "09-30"),
        "Q4": ("12-31", "10-01", "12-31"),
    }

    q_part, year = quarter.split("-")
    end_date = f"{year}-{quarter_dates[q_part][0]}"

    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from webdriver_manager.chrome import ChromeDriverManager
        import requests
    except ImportError:
        log.error("Required packages not installed. Run: pip install selenium requests webdriver-manager")
        return {}

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
    except Exception:
        try:
            options.binary_location = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
            driver = webdriver.Chrome(options=options)
        except Exception:
            options.binary_location = "/usr/bin/google-chrome"
            driver = webdriver.Chrome(options=options)

    downloaded = {}
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    try:
        for i, fund in enumerate(funds):
            fund_name = fund["name"]
            log.info(f"[{i+1}/{len(funds)}] Processing: {fund_name}")

            existing = list(quarter_dir.glob(f"*{fund_name[:20]}*.pdf"))
            if existing:
                log.info(f"  Already downloaded: {existing[0].name}")
                downloaded[fund_name] = str(existing[0])
                continue

            try:
                profile_url = fund.get("profileUrl", "")
                if not profile_url:
                    continue

                full_url = profile_url if profile_url.startswith("http") else f"{BASE_URL}{profile_url}"
                driver.get(full_url)
                time.sleep(3)

                driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.7)")
                time.sleep(2)

                announcements = driver.find_elements(By.CSS_SELECTOR, ".announcements a, .announcement-item a, [class*='announcement'] a")

                if not announcements:
                    all_links = driver.find_elements(By.TAG_NAME, "a")
                    announcements = [
                        a for a in all_links
                        if "quarterly" in (a.text or "").lower()
                        or "statement" in (a.text or "").lower()
                        or end_date in (a.text or "")
                    ]

                for ann in announcements:
                    ann_text = ann.text.strip()
                    ann_href = ann.get_attribute("href") or ""

                    if end_date in ann_text or "quarterly" in ann_text.lower():
                        driver.get(ann_href if ann_href.startswith("http") else f"{BASE_URL}{ann_href}")
                        time.sleep(3)

                        pdf_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='.pdf'], a[href*='fsPdf']")
                        for pdf_link in pdf_links:
                            pdf_url = pdf_link.get_attribute("href")
                            if pdf_url:
                                if not pdf_url.startswith("http"):
                                    pdf_url = f"{BASE_URL}{pdf_url}"

                                safe_name = re.sub(r'[^\w\s-]', '', fund_name)[:50]
                                pdf_path = quarter_dir / f"{safe_name}_{quarter}.pdf"

                                response = session.get(pdf_url, timeout=30)
                                if response.status_code == 200 and len(response.content) > 1000:
                                    with open(pdf_path, "wb") as f:
                                        f.write(response.content)
                                    log.info(f"  Downloaded: {pdf_path.name} ({len(response.content)//1024}KB)")
                                    downloaded[fund_name] = str(pdf_path)
                                    break
                        break

            except Exception as e:
                log.warning(f"  Error processing {fund_name}: {e}")
                continue

            time.sleep(1)

    finally:
        driver.quit()

    log.info(f"Downloaded {len(downloaded)}/{len(funds)} fund reports")
    return downloaded


# ---------------------------------------------------------------------------
# Step 4: Generate Dashboard Data File
# ---------------------------------------------------------------------------
def generate_dashboard_data(funds, holdings_data, quarter):
    """Generate/update the fund_data.json file consumed by the HTML dashboard."""
    log.info("=" * 60)
    log.info("STEP 4: Generating dashboard data file...")
    log.info("=" * 60)

    # Load existing data to merge
    existing_data = {}
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing_data = json.load(f)

    existing_funds = existing_data.get("funds", {})

    for fund_name, holdings in holdings_data.items():
        fund_info = next((f for f in funds if f["name"] == fund_name), None)
        fund_key = re.sub(r'[^a-zA-Z0-9]', '_', fund_name)[:30].lower()

        if fund_key in existing_funds:
            # Merge: add new quarter to existing fund
            if "quarters" not in existing_funds[fund_key]:
                existing_funds[fund_key]["quarters"] = {}
            end_date = date_to_quarter_end(quarter)
            existing_funds[fund_key]["quarters"][quarter] = {
                "period": quarter.replace("-", " "),
                "endDate": end_date,
                "holdings": holdings
            }
        else:
            # New fund
            end_date = date_to_quarter_end(quarter)
            existing_funds[fund_key] = {
                "id": fund_key,
                "name": fund_info["manager"] if fund_info else fund_name,
                "fundName": fund_name,
                "manager": fund_info["manager"] if fund_info else "Unknown",
                "currency": fund_info.get("currency", "SAR") if fund_info else "SAR",
                "shariah": fund_info.get("shariah", "") if fund_info else "",
                "objective": fund_info.get("objective", "Growth") if fund_info else "Growth",
                "fundId": fund_info.get("fundId", "") if fund_info else "",
                "quarters": {
                    quarter: {
                        "period": quarter.replace("-", " "),
                        "endDate": end_date,
                        "holdings": holdings
                    }
                }
            }

    # Update metadata
    funds_with_holdings = sum(
        1 for f in existing_funds.values()
        if any(q.get("holdings") for q in f.get("quarters", {}).values())
    )

    existing_data["metadata"] = {
        "generatedAt": datetime.now().isoformat(),
        "quarter": quarter,
        "totalFunds": len(existing_funds),
        "fundsWithHoldings": funds_with_holdings,
        "source": "Saudi Exchange (Tadawul) - CMA Article 76 Quarterly Disclosures",
        "url": MUTUAL_FUNDS_URL,
        "note": "Run: python scraper.py --local  to process new PDFs"
    }
    existing_data["funds"] = existing_funds

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, indent=2, ensure_ascii=False)

    write_fund_data_js(existing_data)

    log.info(f"Dashboard data saved: {OUTPUT_FILE}")
    log.info(f"  {funds_with_holdings} funds with holdings data (out of {len(existing_funds)} total)")

    return existing_data


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Tadawul Fund Report Scraper & Extractor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scraper.py --local                Process all PDFs in this folder
  python scraper.py --local --use-ai       Use Claude API for extraction (best results)
  python scraper.py --local --quarter Q4-2025   Only process Q4-2025 PDFs
  python scraper.py --list-funds           List all equity funds from Tadawul
  python scraper.py --quarter Q1-2026      Full pipeline for Q1-2026
        """
    )
    parser.add_argument("--local", action="store_true",
                        help="Process PDFs already in this folder (recommended)")
    parser.add_argument("--list-funds", action="store_true",
                        help="Just list all equity funds from Tadawul")
    parser.add_argument("--download-only", action="store_true",
                        help="Only download PDFs (no extraction)")
    parser.add_argument("--extract-only", action="store_true",
                        help="Only extract from already-downloaded PDFs")
    parser.add_argument("--quarter", default=None,
                        help="Target quarter (e.g., Q4-2025). For --local, filters PDFs.")
    parser.add_argument("--use-ai", action="store_true",
                        help="Use Claude API for PDF extraction (most reliable)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit number of funds to process")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("Tadawul Fund Report Scraper & Extractor")
    log.info("=" * 60)

    # ---- LOCAL MODE (recommended) ----
    if args.local:
        log.info("MODE: Local PDF processing")
        if args.quarter:
            log.info(f"Filter: {args.quarter} only")
        # Look in the pdfs/ subfolder first, fall back to script directory
        pdfs_dir = SCRIPT_DIR / "pdfs"
        folder = pdfs_dir if pdfs_dir.exists() else SCRIPT_DIR
        process_local_pdfs(
            pdf_folder=folder,
            use_ai=args.use_ai,
            target_quarter=args.quarter
        )
        return

    # ---- SELENIUM-BASED PIPELINE ----
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not args.quarter:
        args.quarter = "Q4-2025"
    log.info(f"Quarter: {args.quarter}")

    # Step 1: Get fund list
    if args.extract_only:
        funds = load_fund_registry()
    else:
        funds = scrape_fund_list()

    if not funds:
        log.error("No funds found. Exiting.")
        return

    if args.list_funds:
        print(f"\n{'='*80}")
        print(f"Total Saudi Equity/Growth Funds: {len(funds)}")
        print(f"{'='*80}")
        for i, f in enumerate(funds):
            print(f"  {i+1:3d}. {f['name'][:50]:<52s} | {f['manager'][:30]:<32s} | {f['objective']}")
        return

    if args.limit > 0:
        funds = funds[:args.limit]
        log.info(f"Limited to {args.limit} funds")

    # Step 2: Download PDFs
    if not args.extract_only:
        downloaded = download_fund_reports(funds, args.quarter)
    else:
        quarter_dir = PDF_DIR / args.quarter
        downloaded = {}
        if quarter_dir.exists():
            for pdf_file in quarter_dir.glob("*.pdf"):
                fund_name = pdf_file.stem.rsplit("_", 1)[0]
                downloaded[fund_name] = str(pdf_file)
        log.info(f"Found {len(downloaded)} existing PDFs in {quarter_dir}")

    if args.download_only:
        log.info("Download-only mode. Stopping here.")
        return

    # Step 3: Extract holdings
    log.info("=" * 60)
    log.info(f"STEP 3: Extracting holdings from {len(downloaded)} PDFs...")
    log.info("=" * 60)

    holdings_data = {}
    for fund_name, pdf_path in downloaded.items():
        log.info(f"Extracting: {fund_name}")
        holdings = extract_holdings_from_pdf(pdf_path, use_ai=args.use_ai)
        if holdings:
            holdings_data[fund_name] = holdings
            log.info(f"  Extracted {len(holdings)} holdings")
        else:
            log.warning(f"  No holdings extracted from {fund_name}")

    # Step 4: Generate/merge dashboard data
    generate_dashboard_data(funds, holdings_data, args.quarter)

    log.info("=" * 60)
    log.info("Pipeline complete!")
    log.info(f"  Funds processed: {len(holdings_data)}/{len(funds)}")
    log.info(f"  Output: {OUTPUT_FILE}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
