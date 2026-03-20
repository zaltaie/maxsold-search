"""
Local HTML report generator.
Creates a standalone HTML file with all listings and opens it in the browser.
No accounts, no API keys, no configuration needed.
"""

import logging
import os
import webbrowser
from datetime import datetime, timedelta, timezone

from db.database import get_session
from db.models import BidHistory, Listing, PriceResearch

logger = logging.getLogger(__name__)

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

CONDITION_COLORS = {
    "Excellent": "#28a745",
    "Good": "#ffc107",
    "Fair": "#fd7e14",
    "Parts Only": "#dc3545",
}


def _ensure_reports_dir():
    os.makedirs(REPORTS_DIR, exist_ok=True)


def _bid_history_chart_html(bid_records: list) -> str:
    """Generate a simple inline SVG sparkline for bid history."""
    if not bid_records or len(bid_records) < 2:
        return ""

    amounts = [r.bid_amount for r in bid_records]
    min_val = min(amounts)
    max_val = max(amounts)
    val_range = max_val - min_val if max_val > min_val else 1

    width = 200
    height = 40
    padding = 2
    chart_w = width - 2 * padding
    chart_h = height - 2 * padding

    n = len(amounts)
    points = []
    for i, val in enumerate(amounts):
        x = padding + (i / (n - 1)) * chart_w
        y = padding + chart_h - ((val - min_val) / val_range) * chart_h
        points.append(f"{x:.1f},{y:.1f}")

    polyline = " ".join(points)
    first = amounts[0]
    last = amounts[-1]
    change_pct = ((last - first) / first * 100) if first > 0 else 0
    color = "#dc3545" if change_pct > 0 else "#28a745"

    return f"""
    <div style="margin:8px 0;">
      <div style="font-size:11px;color:#868e96;margin-bottom:2px;">Bid History ({n} updates)</div>
      <svg width="{width}" height="{height}" style="background:#f8f9fa;border-radius:4px;">
        <polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="2" />
      </svg>
      <div style="font-size:11px;color:{color};">${first:.2f} → ${last:.2f} ({change_pct:+.0f}%)</div>
    </div>"""


def _listing_card_html(listing, research, bid_records=None) -> str:
    """Generate HTML for a single listing card."""
    condition = research.condition_score if research else "Unknown"
    condition_color = CONDITION_COLORS.get(condition, "#6c757d")
    est_value = f"${research.estimated_value:.2f}" if research else "—"
    max_bid = f"${research.max_bid_price:.2f}" if research else "—"
    fb_price = f"${research.fb_marketplace_ceiling:.2f}" if research else "—"
    summary = research.condition_notes if research else ""
    deal_flag = research.deal_flag if research else False

    # Photo
    photo_html = '<div style="width:100%;height:180px;background:#e9ecef;display:flex;align-items:center;justify-content:center;color:#868e96;font-size:14px;">No photo</div>'
    if listing.photo_urls and isinstance(listing.photo_urls, list) and listing.photo_urls:
        photo_html = f'<img src="{listing.photo_urls[0]}" alt="{listing.title}" style="width:100%;height:180px;object-fit:cover;" />'

    # Deal badge
    deal_html = ""
    if deal_flag:
        deal_html = '<span style="position:absolute;top:8px;right:8px;background:#28a745;color:#fff;padding:4px 10px;border-radius:12px;font-size:12px;font-weight:bold;">DEAL</span>'

    # End time
    end_str = ""
    if listing.auction_end_time:
        end_str = listing.auction_end_time.strftime("%b %d, %I:%M %p") if isinstance(listing.auction_end_time, datetime) else str(listing.auction_end_time)

    # Bid history chart
    bid_chart = _bid_history_chart_html(bid_records) if bid_records else ""

    return f"""
    <div style="background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.1);position:relative;">
      {deal_html}
      {photo_html}
      <div style="padding:14px;">
        <h3 style="margin:0 0 6px 0;font-size:15px;color:#212529;line-height:1.3;">{listing.title}</h3>
        <div style="display:flex;gap:8px;margin-bottom:10px;">
          <span style="background:{condition_color};color:#fff;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:bold;">{condition}</span>
          <span style="color:#868e96;font-size:12px;">{listing.category or ''}</span>
        </div>
        <table style="width:100%;font-size:13px;color:#495057;">
          <tr><td>Current Bid</td><td style="text-align:right;font-weight:bold;color:#212529;">${listing.current_bid:.2f}</td></tr>
          <tr><td>Est. Resale</td><td style="text-align:right;font-weight:bold;color:#28a745;">{est_value}</td></tr>
          <tr><td>Max Bid Target</td><td style="text-align:right;">{max_bid}</td></tr>
          <tr><td>FB Marketplace</td><td style="text-align:right;">{fb_price}</td></tr>
        </table>
        {bid_chart}
        <p style="font-size:12px;color:#868e96;margin:8px 0 0 0;">{summary}</p>
        {f'<p style="font-size:11px;color:#dc3545;margin:4px 0 0 0;">Ends: {end_str}</p>' if end_str else ''}
        <a href="{listing.maxsold_url}" target="_blank"
           style="display:block;text-align:center;background:#339af0;color:#fff;padding:8px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:bold;margin-top:10px;">
          View on Maxsold
        </a>
      </div>
    </div>"""


def generate_report(hours_back: int = 24, open_browser: bool = True) -> str:
    """
    Generate an HTML report of recent listings and save it to reports/.

    Args:
        hours_back: How many hours of listings to include (default 24).
        open_browser: Whether to auto-open the report in the browser.

    Returns:
        Path to the generated HTML file.
    """
    _ensure_reports_dir()

    session = get_session()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        listings = (
            session.query(Listing)
            .filter(Listing.created_at >= cutoff)
            .order_by(Listing.created_at.desc())
            .all()
        )

        # Separate deals from regular listings
        deals = []
        regular = []

        for listing in listings:
            research = (
                session.query(PriceResearch)
                .filter_by(listing_id=listing.id)
                .order_by(PriceResearch.created_at.desc())
                .first()
            )
            bid_records = (
                session.query(BidHistory)
                .filter_by(listing_id=listing.id)
                .order_by(BidHistory.recorded_at.asc())
                .all()
            )
            if research and research.deal_flag:
                deals.append((listing, research, bid_records))
            else:
                regular.append((listing, research, bid_records))

        today_str = datetime.now().strftime("%B %d, %Y")
        time_str = datetime.now().strftime("%I:%M %p")

        # Build deals section
        deals_html = ""
        if deals:
            cards = "".join(_listing_card_html(l, r, b) for l, r, b in deals)
            deals_html = f"""
            <h2 style="color:#28a745;margin:32px 0 16px 0;font-size:20px;">
              Deals Found ({len(deals)})
            </h2>
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;">
              {cards}
            </div>"""

        # Build all listings section
        all_cards = "".join(_listing_card_html(l, r, b) for l, r, b in deals + regular)
        all_html = ""
        if listings:
            all_html = f"""
            <h2 style="color:#495057;margin:32px 0 16px 0;font-size:20px;">
              All Listings ({len(listings)})
            </h2>
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;">
              {all_cards}
            </div>"""
        else:
            all_html = """
            <div style="text-align:center;padding:60px 20px;color:#868e96;">
              <p style="font-size:18px;">No camera listings found in the last 24 hours.</p>
              <p>Try running a scan: <code>python main.py --scan</code></p>
            </div>"""

        # Stats
        total_deals = len(deals)
        total_listings = len(listings)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Camera Finder Report — {today_str}</title>
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;background:#f8f9fa;">

  <header style="background:linear-gradient(135deg,#1c7ed6,#339af0);color:#fff;padding:24px 32px;">
    <h1 style="margin:0;font-size:24px;">Camera Finder — Vancouver</h1>
    <p style="margin:6px 0 0 0;opacity:0.85;font-size:14px;">{today_str} at {time_str}</p>
  </header>

  <div style="max-width:1100px;margin:0 auto;padding:0 20px 40px 20px;">

    <div style="display:flex;gap:16px;margin:24px 0;flex-wrap:wrap;">
      <div style="background:#fff;border-radius:8px;padding:16px 24px;box-shadow:0 1px 3px rgba(0,0,0,0.08);flex:1;min-width:140px;">
        <div style="font-size:28px;font-weight:bold;color:#212529;">{total_listings}</div>
        <div style="font-size:13px;color:#868e96;text-transform:uppercase;">Listings</div>
      </div>
      <div style="background:#fff;border-radius:8px;padding:16px 24px;box-shadow:0 1px 3px rgba(0,0,0,0.08);flex:1;min-width:140px;">
        <div style="font-size:28px;font-weight:bold;color:#28a745;">{total_deals}</div>
        <div style="font-size:13px;color:#868e96;text-transform:uppercase;">Deals</div>
      </div>
    </div>

    {deals_html}
    {all_html}

  </div>

  <footer style="text-align:center;padding:20px;color:#adb5bd;font-size:12px;border-top:1px solid #e9ecef;">
    Camera Finder — Generated {today_str} at {time_str}
  </footer>

</body>
</html>"""

        # Write report
        filename = f"report_{datetime.now().strftime('%Y-%m-%d_%H%M')}.html"
        filepath = os.path.join(REPORTS_DIR, filename)
        with open(filepath, "w") as f:
            f.write(html)

        logger.info(f"Report generated: {filepath}")

        if open_browser:
            try:
                webbrowser.open(f"file://{os.path.abspath(filepath)}")
                logger.info("Report opened in browser")
            except Exception:
                logger.info(f"Open manually: file://{os.path.abspath(filepath)}")

        return filepath

    finally:
        session.close()
