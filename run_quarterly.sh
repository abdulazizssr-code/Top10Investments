#!/bin/bash
# ================================================================
#  Tadawul Fund Scraper — Quarterly Automation Script (Linux/Mac)
# ================================================================
#  Auto-detects the latest ended quarter and runs the scraper.
#
#  SETUP — Add to crontab:
#    crontab -e
#    # Run on the 20th of Jan, Apr, Jul, Oct at 9:00 AM
#    0 9 20 1,4,7,10 * /path/to/run_quarterly.sh >> /path/to/scraper_cron.log 2>&1
#
#  Or for monthly runs (will skip if no new quarter):
#    0 9 20 * * /path/to/run_quarterly.sh >> /path/to/scraper_cron.log 2>&1
# ================================================================

set -e

# Navigate to script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Detect current quarter (the one that just ended)
MONTH=$(date +%m)
YEAR=$(date +%Y)

if [ "$MONTH" -le 3 ]; then
    PREV_YEAR=$((YEAR - 1))
    QUARTER="Q4-${PREV_YEAR}"
elif [ "$MONTH" -le 6 ]; then
    QUARTER="Q1-${YEAR}"
elif [ "$MONTH" -le 9 ]; then
    QUARTER="Q2-${YEAR}"
else
    QUARTER="Q3-${YEAR}"
fi

echo "================================================================"
echo " Tadawul Fund Scraper — Automated Quarterly Run"
echo " Quarter: $QUARTER"
echo " Date: $(date)"
echo "================================================================"

# Activate virtual environment if it exists
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# Run the scraper
if python scripts/scraper.py --quarter "$QUARTER"; then
    echo "[SUCCESS] Scraper completed for $QUARTER"
else
    echo "[WARN] Basic extraction failed. Trying with AI..."
    python scripts/scraper.py --quarter "$QUARTER" --use-ai || echo "[ERROR] AI extraction also failed"
fi

echo "================================================================"
echo " Done. fund_data.json updated at $(date)"
echo " Open index.html in a browser to view the dashboard."
echo "================================================================"
