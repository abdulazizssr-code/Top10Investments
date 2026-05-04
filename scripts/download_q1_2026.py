#!/usr/bin/env python3
"""
Download Q1-2026 Quarterly Reports from Tadawul
================================================
Downloads the latest quarterly factsheet PDFs for all Saudi equity funds.
Q1-2026 reports are published in April 2026, so filenames contain dates
in the 2026-04 range.

Usage:
    python download_q1_2026.py                  # Download all new Q1-2026 PDFs
    python download_q1_2026.py --test           # Test with 3 funds only
    python download_q1_2026.py --list           # Just list funds

Requirements:
    pip install selenium webdriver-manager requests
"""

import os
import re
import sys
import json
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("ERROR: pip install requests"); sys.exit(1)

BASE_URL = "https://www.saudiexchange.sa"
SCRIPT_DIR = Path(__file__).parent
PDF_DIR = SCRIPT_DIR / "pdfs"
LISTING_URL = f"{BASE_URL}/wps/portal/saudiexchange/ourmarkets/funds-market-watch/mutual-funds?locale=en"
EQUITY_KEYWORDS = ['equity', 'etf', 'stock', 'shares']

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("Q1-2026-Downloader")


def pdf_to_quarter(pdf_path):
    match = re.search(r'_(\d{4})-(\d{2})-(\d{2})_', pdf_path)
    if not match: return "Unknown"
    year, month = int(match.group(1)), int(match.group(2))
    if 1 <= month <= 3: return f"Q4-{year-1}"
    elif 4 <= month <= 6: return f"Q1-{year}"
    elif 7 <= month <= 9: return f"Q2-{year}"
    else: return f"Q3-{year}"


def scrape_fund_list(driver):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    log.info("Loading Tadawul mutual funds listing page...")
    driver.get(LISTING_URL)

    try:
        WebDriverWait(driver, 20).until(
            lambda d: len(d.find_elements(By.CSS_SELECTOR, 'table a[href*="company-profile-mutual-fund"]')) > 10
        )
    except: log.warning("Table may not have fully loaded")

    time.sleep(3)

    links = driver.find_elements(By.CSS_SELECTOR, 'table a[href*="company-profile-mutual-fund"]')
    log.info(f"Found {len(links)} total fund links")

    all_funds = []
    for link in links:
        try:
            name = link.text.strip()
            href = link.get_attribute('href')
            if not name or not href: continue
            row = link.find_element(By.XPATH, './ancestor::tr')
            cells = row.find_elements(By.TAG_NAME, 'td')
            manager = cells[1].text.strip() if len(cells) > 1 else ''
            all_funds.append([name, manager, href])
        except: continue

    equity_funds = [f for f in all_funds if any(kw in f[0].lower() for kw in EQUITY_KEYWORDS)]
    log.info(f"Filtered to {len(equity_funds)} equity funds")
    return equity_funds


def extract_pdf_links(html):
    factsheet_pattern = r'href=["\'](/Resources/mfpdfs/factsheet/[^"\']+\.pdf)'
    announcement_pattern = r'href=["\'](/Resources/fsPdf/[^"\']+\.pdf)'
    factsheets = re.findall(factsheet_pattern, html, re.IGNORECASE)
    announcements = re.findall(announcement_pattern, html, re.IGNORECASE)
    return factsheets, announcements


def init_driver(download_dir):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    os.makedirs(download_dir, exist_ok=True)
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0')
    options.add_experimental_option("prefs", {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True,
    })

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    except:
        driver = webdriver.Chrome(options=options)

    driver.execute_cdp_cmd("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": download_dir})
    return driver


def download_pdf(session, pdf_url, save_path):
    try:
        resp = session.get(pdf_url, timeout=60, stream=True)
        if resp.status_code == 200 and 'html' not in resp.headers.get('Content-Type', '').lower():
            with open(save_path, 'wb') as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            return True
    except Exception as e:
        log.error(f"  Download failed: {e}")
    return False


def sanitize_name(name):
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    return re.sub(r'\s+', '_', name)[:80]


def main():
    parser = argparse.ArgumentParser(description="Download Q1-2026 fund quarterly reports from Tadawul")
    parser.add_argument('--test', action='store_true', help='Test with 3 funds')
    parser.add_argument('--list', action='store_true', help='Just list funds')
    parser.add_argument('--output', type=str, default=str(PDF_DIR))
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    dl_dir = str(output_dir / "_chrome_downloads")
    driver = init_driver(dl_dir)

    try:
        funds = scrape_fund_list(driver)
    except Exception as e:
        log.error(f"Failed to scrape fund list: {e}")
        driver.quit()
        sys.exit(1)

    if args.test: funds = funds[:3]

    if args.list:
        print(f"\n{'='*80}")
        for i, (name, mgr, _) in enumerate(funds, 1):
            print(f"  {i:3d}. {name:<55s} {mgr}")
        print(f"{'='*80}\n  Total: {len(funds)} equity funds\n")
        driver.quit()
        return

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0',
        'Referer': BASE_URL,
    })

    stats = {"found": 0, "downloaded": 0, "skipped": 0, "errors": 0}

    # Pre-scan all existing folders for Q1-2026 PDFs to skip already-downloaded funds
    existing_q1_funds = set()
    if output_dir.exists():
        for fund_folder in output_dir.iterdir():
            if fund_folder.is_dir() and not fund_folder.name.startswith('_'):
                for pdf_file in fund_folder.glob('*.pdf'):
                    if pdf_to_quarter(pdf_file.name) == "Q1-2026":
                        existing_q1_funds.add(fund_folder.name.lower())
                        break
    log.info(f"Found {len(existing_q1_funds)} funds already with Q1-2026 PDFs — will skip them")

    print(f"\n{'='*70}")
    print(f"  Q1-2026 PDF Downloader — {len(funds)} equity funds")
    print(f"  Already have Q1-2026: {len(existing_q1_funds)} funds")
    print(f"  Output: {output_dir}")
    print(f"{'='*70}\n")

    for i, (name, manager, fund_url) in enumerate(funds, 1):
        # Quick skip: check if this fund name matches any existing Q1-2026 folder
        sanitized = sanitize_name(name).lower()
        if sanitized in existing_q1_funds:
            log.info(f"[{i}/{len(funds)}] {name} — skipped (already have Q1-2026)")
            stats["skipped"] += 1
            continue

        log.info(f"[{i}/{len(funds)}] {name}")

        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait

            driver.get(fund_url)
            WebDriverWait(driver, 15).until(lambda d: len(d.page_source) > 5000)

            # Click Fact Sheet tab if exists
            try:
                tabs = driver.find_elements(By.XPATH, "//*[contains(text(), 'Fact Sheet')]")
                for tab in tabs:
                    if tab.is_displayed():
                        tab.click()
                        time.sleep(2)
                        break
            except: pass

            time.sleep(3)
            html = driver.page_source
        except Exception as e:
            log.error(f"  Page load failed: {e}")
            stats["errors"] += 1
            continue

        factsheets, announcements = extract_pdf_links(html)
        all_pdfs = factsheets or announcements

        if not all_pdfs:
            log.warning(f"  No PDFs found")
            continue

        # Filter for Q1-2026 PDFs only (dates in April 2026 range)
        q1_pdfs = [p for p in all_pdfs if pdf_to_quarter(p) == "Q1-2026"]

        if not q1_pdfs:
            # Also check for any new 2026-04 dated PDFs
            q1_pdfs = [p for p in all_pdfs if '2026-04' in p or '2026-05' in p]

        if not q1_pdfs:
            log.info(f"  No Q1-2026 reports yet")
            continue

        # Prefer English
        en_pdfs = [p for p in q1_pdfs if p.lower().endswith('_en.pdf')]
        target = en_pdfs[0] if en_pdfs else q1_pdfs[0]

        stats["found"] += 1
        fund_dir = output_dir / sanitize_name(name)
        fund_dir.mkdir(exist_ok=True)

        filename = target.split('/')[-1]
        save_path = fund_dir / filename

        # Skip if exact file exists
        if save_path.exists():
            log.info(f"  Already have: {filename}")
            stats["skipped"] += 1
            continue

        # Skip if ANY Q1-2026 PDF already exists in this fund's folder
        existing_q1 = [f for f in fund_dir.iterdir()
                       if f.suffix.lower() == '.pdf' and pdf_to_quarter(f.name) == "Q1-2026"]
        if existing_q1:
            log.info(f"  Already have Q1-2026: {existing_q1[0].name}")
            stats["skipped"] += 1
            continue

        pdf_url = BASE_URL + target
        if download_pdf(session, pdf_url, save_path):
            log.info(f"  Downloaded: {filename} ({save_path.stat().st_size:,} bytes)")
            stats["downloaded"] += 1
        else:
            log.warning(f"  Failed, trying Selenium...")
            try:
                import shutil
                before = set(os.listdir(dl_dir)) if os.path.exists(dl_dir) else set()
                driver.get(pdf_url)
                time.sleep(5)
                after = set(os.listdir(dl_dir)) if os.path.exists(dl_dir) else set()
                new = [f for f in (after - before) if not f.endswith('.crdownload')]
                if new:
                    shutil.move(str(Path(dl_dir) / new[0]), str(save_path))
                    log.info(f"  Downloaded via Selenium: {filename}")
                    stats["downloaded"] += 1
                else:
                    stats["errors"] += 1
            except Exception as e:
                log.error(f"  Selenium download failed: {e}")
                stats["errors"] += 1

        time.sleep(2)

    driver.quit()

    print(f"\n{'='*70}")
    print(f"  DOWNLOAD COMPLETE")
    print(f"  Q1-2026 PDFs found:      {stats['found']}")
    print(f"  Downloaded:              {stats['downloaded']}")
    print(f"  Already had:             {stats['skipped']}")
    print(f"  Errors:                  {stats['errors']}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
