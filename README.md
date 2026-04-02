# Tadawul Fund Investment Tracker

Interactive dashboard tracking the **Top 10 Holdings** of all ~154 Saudi public equity fund managers listed on Tadawul (Saudi Exchange).

## Features

- **Dashboard** -- Overview of all funds with stats, search, filtering, and a weight trend chart for the top 10 most-held stocks across 16 quarters
- **Individual Funds** -- Drill into any fund's top 10 holdings with bar charts and detail tables
- **Consolidated View** -- See the most popular stocks across all fund managers (or filter by a specific manager like Al Rajhi Capital, Jadwa Investment, etc.), with "count by manager" grouping
- **Stock Tracker** -- Search or pick any stock, track its average weight and fund count over time. Compare up to 10 stocks side by side
- **Momentum Scanner** -- Discover which stocks are gaining or losing fund holders quarter-over-quarter

## Data

- **154 funds** across **41 fund managers**
- **16 quarters** of historical data
- Holdings extracted from CMA Article 76 quarterly disclosure PDFs published on Tadawul

## Usage

Open `index.html` in any modern browser. No server required -- works directly from the file system.

## Tech Stack

- Pure HTML/CSS/JavaScript (no frameworks)
- Chart.js 4.4.1 for visualizations
- Data loaded from `fund_data.min.js`

## License

MIT
