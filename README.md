# Camera Finder — Vancouver

Automated Maxsold camera auction scraper with pricing research and deal alerts.

Scrapes [Maxsold.com](https://maxsold.com) for camera-related auction listings in the Vancouver region, compares against eBay sold prices, scores condition, flags deals, and generates HTML reports you can view in your browser.

**Zero configuration required** — no API keys, no accounts, no cost. Install and run.

## Quick Start

```bash
pip install -r requirements.txt
playwright install chromium
python main.py --scan
```

That's it. A report will open in your browser showing camera listings with pricing data.

## Usage

```bash
# Run a scan now and open the report
python main.py --scan

# Generate a report from existing data (no new scan)
python main.py --report

# Show a terminal dashboard
python main.py --dashboard

# Start the daily scheduler (auto-scans at 8am)
python main.py
```

## How It Works

1. **Scraping**: Uses Maxsold's internal Algolia search API to find active auctions near Vancouver. Matches listings against configurable camera keywords across 5 categories (digital, SLR/film, cine/8mm, accessories, estate/fuzzy).

2. **Pricing**: Scrapes eBay completed/sold listings directly (no API key needed) with automatic fallback to broader search terms. Prices are converted from USD to CAD.

3. **Analysis**: Rule-based pricing engine compares eBay comps to the current bid, scores condition from listing description keywords, and flags deals below your margin target (default: buy at 50% of resale value).

4. **Reports**: Generates standalone HTML reports with listing cards, photos, pricing breakdowns, and deal badges. Opens in your browser — no server needed.

5. **Email (optional)**: If you configure Gmail SMTP, you'll also get instant alerts for watchlist matches and a daily digest email.

## Project Structure

```
├── main.py                    # Entry point (--scan, --report, --dashboard)
├── config.yaml                # Keywords, watchlist, settings
├── requirements.txt           # Python dependencies
├── db/
│   ├── database.py            # SQLite + SQLAlchemy setup
│   └── models.py              # Database models
├── scraper/
│   ├── maxold.py              # Maxsold scraper (Algolia API + Playwright fallback)
│   └── scheduler.py           # Pipeline orchestration
├── pricing/
│   ├── ebay.py                # eBay sold listings scraper (no API key)
│   └── claude_ai.py           # Rule-based pricing engine
├── notifications/
│   ├── report.py              # HTML report generator
│   └── email.py               # Gmail SMTP (optional)
├── dashboard/
│   └── terminal.py            # Rich terminal dashboard
└── reports/                   # Generated HTML reports (gitignored)
```

## Configuration

Edit `config.yaml` to customize:

- **Keywords**: Add/remove camera search terms per category
- **Watchlist**: Specific models you want instant alerts on (e.g., Canon AE-1, Leica)
- **Margin target**: Adjust `business.margin_target` (default 0.50 = buy at 50% of resale)
- **Scan time**: Change `maxsold.scan_time` (default "08:00")

### Optional: Email Notifications

If you want email alerts in addition to HTML reports:

1. Enable 2-Step Verification on your Google Account
2. Go to Google Account > Security > App Passwords
3. Create a password named "camera-finder"
4. Fill in the `email` section of `config.yaml`:

```yaml
email:
  sender_address: "you@gmail.com"
  app_password: "your-16-char-app-password"
  recipient_address: "you@gmail.com"
```

## Dependencies

Only 6 packages — no paid APIs:

- `playwright` — browser automation for Maxsold token extraction
- `apscheduler` — daily scan scheduling
- `sqlalchemy` — SQLite database
- `requests` — eBay scraping + API calls
- `pyyaml` — config file parsing
- `rich` — terminal dashboard
