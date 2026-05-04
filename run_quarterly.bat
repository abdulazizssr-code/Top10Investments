@echo off
REM ================================================================
REM  Fund Tracker - Process Local PDFs
REM ================================================================
REM  Drop your quarterly report PDFs into this folder, then
REM  double-click this file to extract holdings and update the dashboard.
REM
REM  For better results with Arabic/chart-based PDFs, run from cmd:
REM    python scripts\scraper.py --local --use-ai
REM ================================================================

cd /d "%~dp0"

echo ============================================================
echo   Fund Tracker - Quarterly Update
echo ============================================================
echo.
echo Processing all PDF files in this folder...
echo.

python scripts\scraper.py --local

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Something went wrong. Check that Python is installed
    echo and pdfplumber is available: pip install pdfplumber
    echo.
)

echo.
echo ============================================================
echo   Done! Open index.html to see your updated dashboard.
echo ============================================================
echo.
pause
