# Camera Finder — Vancouver

Automated Maxsold camera auction scraper with AI-powered pricing research and email alerts.

Scrapes [Maxsold.com](https://maxsold.com) for camera-related auction listings in the Vancouver region, uses eBay sold data and Claude AI to estimate fair market value, and sends email notifications when deals are found.

## Prerequisites

- Python 3.11+
- A Gmail account with 2FA enabled (for email alerts)

## Installation

```bash
git clone <repo-url>
cd maxsold-search
pip install -r requirements.txt
playwright install chromium
```

## Configuration

Edit `config.yaml` and fill in your credentials:

### eBay API Credentials

1. Register for free at [developer.ebay.com](https://developer.ebay.com)
2. Create an application to get your App ID and App Secret
3. Add both to `config.yaml` under the `ebay` section

### Anthropic API Key

1. Get your API key at [console.anthropic.com](https://console.anthropic.com)
2. Add it to `config.yaml` under `claude.api_key`

### Gmail App Password

1. Go to your Google Account > Security > 2-Step Verification
2. Scroll to "App Passwords" and create one named "camera-finder"
3. Add the 16-character password to `config.yaml` under `email.app_password`
4. Set `email.sender_address` and `email.recipient_address` to your Gmail address

## Run

```bash
python main.py
```

The app will:
1. Initialize the SQLite database
2. Run an initial scrape of Maxsold Vancouver auctions
3. Display a Rich terminal dashboard
4. Continue scraping every 30 minutes
5. Send email alerts for watchlist matches and deals
6. Send a daily digest email at 8am

## Project Structure

```
├── main.py                    # Entry point
├── config.yaml                # Credentials and settings
├── requirements.txt           # Python dependencies
├── db/
│   ├── database.py            # SQLite + SQLAlchemy setup
│   └── models.py              # Database models (Listing, PriceResearch, BidHistory, Watchlist)
├── scraper/
│   ├── maxold.py              # Maxsold scraper (Algolia API + Playwright fallback)
│   └── scheduler.py           # APScheduler pipeline orchestration
├── pricing/
│   ├── ebay.py                # eBay Browse API for sold comparables
│   └── claude_ai.py           # Claude AI pricing and condition analysis
├── notifications/
│   └── email.py               # Gmail SMTP instant alerts and daily digest
└── dashboard/
    └── terminal.py            # Rich terminal dashboard UI
```

## How It Works

1. **Scraping**: Uses Maxsold's internal Algolia search API to find active auctions near Vancouver. Falls back to Playwright browser automation if needed. Matches listings against configurable camera keywords across 5 categories (digital, SLR/film, cine/8mm, accessories, estate/fuzzy).

2. **Pricing**: For each new listing, fetches sold comparable listings from the eBay Browse API (with automatic fallback to broader search terms). Prices are converted from USD to CAD.

3. **AI Analysis**: Sends listing details and eBay comp data to Claude for condition assessment, value estimation, and deal flagging based on a 50% margin target.

4. **Alerts**: Sends HTML email alerts (mobile-friendly, inline CSS) when a listing matches the watchlist or is flagged as a deal. Sends a daily digest of all new listings at 8am.

5. **Dashboard**: Displays a live Rich terminal UI with today's listings, watchlist, and statistics.

## Customization

- **Keywords**: Edit the `keywords` section in `config.yaml` to add/remove camera terms
- **Watchlist**: Edit the `watchlist` section for specific models you want instant alerts on
- **Margin target**: Adjust `business.margin_target` (default 0.50 = buy at 50% of resale value)
- **Scrape interval**: Change `maxsold.scrape_interval_minutes` (default 30)
- **Digest time**: Change `email.digest_hour` (default 8 = 8am)
