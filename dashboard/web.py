"""
Lightweight web dashboard for Camera Finder.
Run with: python main.py --web
Accessible from your phone at http://<your-ip>:5050
"""

import logging
from datetime import datetime, timedelta, timezone

from db.database import get_session
from db.models import BidHistory, Listing, PriceResearch, Watchlist

logger = logging.getLogger(__name__)

CONDITION_COLORS = {
    "Excellent": "#28a745",
    "Good": "#ffc107",
    "Fair": "#fd7e14",
    "Parts Only": "#dc3545",
}


def _build_listing_rows_html(session, hours_back=24):
    """Build HTML table rows for all recent listings."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    listings = (
        session.query(Listing)
        .filter(Listing.created_at >= cutoff)
        .order_by(Listing.created_at.desc())
        .all()
    )

    if not listings:
        return "<tr><td colspan='8' style='text-align:center;padding:40px;color:#868e96;'>No listings found in the last 24 hours.</td></tr>", 0, 0

    rows = ""
    deal_count = 0
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

        condition = research.condition_score if research else "?"
        condition_color = CONDITION_COLORS.get(condition, "#6c757d")
        est_value = f"${research.estimated_value:.2f}" if research else "—"
        max_bid = f"${research.max_bid_price:.2f}" if research else "—"
        deal = research.deal_flag if research else False
        if deal:
            deal_count += 1

        # Photo thumbnail
        photo_html = '<div style="width:50px;height:50px;background:#e9ecef;border-radius:4px;"></div>'
        if listing.photo_urls and isinstance(listing.photo_urls, list) and listing.photo_urls:
            photo_html = f'<img src="{listing.photo_urls[0]}" style="width:50px;height:50px;object-fit:cover;border-radius:4px;" />'

        # End time
        end_str = ""
        if listing.auction_end_time:
            now = datetime.now(timezone.utc)
            if isinstance(listing.auction_end_time, datetime):
                if listing.auction_end_time > now:
                    delta = listing.auction_end_time - now
                    hours_left = delta.total_seconds() / 3600
                    if hours_left < 2:
                        end_str = f'<span style="color:#dc3545;font-weight:bold;">{hours_left:.1f}h left</span>'
                    else:
                        end_str = listing.auction_end_time.strftime("%b %d, %I:%M %p")
                else:
                    end_str = '<span style="color:#868e96;">Ended</span>'

        # Bid trend
        bid_trend = ""
        if len(bid_records) >= 2:
            first = bid_records[0].bid_amount
            last = bid_records[-1].bid_amount
            if first > 0:
                pct = ((last - first) / first) * 100
                color = "#dc3545" if pct > 0 else "#28a745"
                bid_trend = f'<span style="color:{color};font-size:11px;">({pct:+.0f}%)</span>'

        deal_badge = '<span style="background:#28a745;color:#fff;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:bold;">DEAL</span>' if deal else ''

        rows += f"""<tr style="border-bottom:1px solid #e9ecef;">
            <td style="padding:10px 8px;">{photo_html}</td>
            <td style="padding:10px 8px;">
                <a href="{listing.maxsold_url}" target="_blank" style="color:#339af0;text-decoration:none;font-weight:bold;">{listing.title}</a>
                <div style="font-size:11px;color:#868e96;">{listing.category or ''}</div>
            </td>
            <td style="padding:10px 8px;text-align:right;font-weight:bold;">${listing.current_bid:.2f} {bid_trend}</td>
            <td style="padding:10px 8px;text-align:right;color:#28a745;">{est_value}</td>
            <td style="padding:10px 8px;text-align:right;">{max_bid}</td>
            <td style="padding:10px 8px;text-align:center;">
                <span style="background:{condition_color};color:#fff;padding:2px 8px;border-radius:8px;font-size:11px;">{condition}</span>
            </td>
            <td style="padding:10px 8px;text-align:center;">{deal_badge}</td>
            <td style="padding:10px 8px;text-align:center;font-size:12px;">{end_str}</td>
        </tr>"""

    return rows, len(listings), deal_count


def _build_watchlist_html(session):
    """Build watchlist HTML."""
    items = session.query(Watchlist).all()
    if not items:
        return '<span style="color:#868e96;">No models on watchlist</span>'
    return " ".join(
        f'<span style="background:#e9ecef;padding:4px 10px;border-radius:12px;font-size:13px;margin:4px;">{item.camera_model}</span>'
        for item in items
    )


def build_dashboard_html():
    """Generate a complete HTML dashboard page."""
    session = get_session()
    try:
        rows_html, total_listings, deal_count = _build_listing_rows_html(session)
        watchlist_html = _build_watchlist_html(session)
        total_all = session.query(Listing).count()
        now_str = datetime.now().strftime("%B %d, %Y at %I:%M %p")

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Camera Finder — Dashboard</title>
  <meta http-equiv="refresh" content="300">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; background: #f8f9fa; }}
    .header {{ background: linear-gradient(135deg, #1c7ed6, #339af0); color: #fff; padding: 20px 24px; }}
    .header h1 {{ margin: 0; font-size: 22px; }}
    .header p {{ margin: 4px 0 0; opacity: 0.85; font-size: 13px; }}
    .container {{ max-width: 1200px; margin: 0 auto; padding: 16px; }}
    .stats {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
    .stat-card {{ background: #fff; border-radius: 8px; padding: 16px 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); flex: 1; min-width: 120px; }}
    .stat-val {{ font-size: 28px; font-weight: bold; color: #212529; }}
    .stat-label {{ font-size: 12px; color: #868e96; text-transform: uppercase; }}
    .card {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 16px; overflow: hidden; }}
    .card-header {{ padding: 14px 20px; font-weight: bold; font-size: 16px; border-bottom: 1px solid #e9ecef; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ padding: 10px 8px; text-align: left; font-size: 11px; color: #868e96; text-transform: uppercase; background: #f8f9fa; }}
    .watchlist {{ padding: 16px 20px; display: flex; flex-wrap: wrap; gap: 4px; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>Camera Finder — Vancouver</h1>
    <p>Dashboard — {now_str} — Auto-refreshes every 5 minutes</p>
  </div>
  <div class="container">
    <div class="stats">
      <div class="stat-card">
        <div class="stat-val">{total_listings}</div>
        <div class="stat-label">Today</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" style="color:#28a745;">{deal_count}</div>
        <div class="stat-label">Deals</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{total_all}</div>
        <div class="stat-label">All Time</div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">Today's Listings</div>
      <table>
        <thead>
          <tr>
            <th></th>
            <th>Title</th>
            <th style="text-align:right;">Bid</th>
            <th style="text-align:right;">Est. Value</th>
            <th style="text-align:right;">Max Bid</th>
            <th style="text-align:center;">Condition</th>
            <th style="text-align:center;">Deal</th>
            <th style="text-align:center;">Ends</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>

    <div class="card">
      <div class="card-header">Watchlist</div>
      <div class="watchlist">{watchlist_html}</div>
    </div>
  </div>
</body>
</html>"""
    finally:
        session.close()


def run_web_dashboard(host="0.0.0.0", port=5050):
    """Start a simple web server for the dashboard."""
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/" or self.path == "/dashboard":
                html = build_dashboard_html()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode())
            elif self.path == "/api/listings":
                import json
                session = get_session()
                try:
                    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                    listings = session.query(Listing).filter(Listing.created_at >= cutoff).all()
                    data = []
                    for l in listings:
                        r = session.query(PriceResearch).filter_by(listing_id=l.id).order_by(PriceResearch.created_at.desc()).first()
                        data.append({
                            "title": l.title,
                            "category": l.category,
                            "current_bid": l.current_bid,
                            "estimated_value": r.estimated_value if r else None,
                            "max_bid_price": r.max_bid_price if r else None,
                            "condition": r.condition_score if r else None,
                            "deal": r.deal_flag if r else False,
                            "url": l.maxsold_url,
                            "end_time": str(l.auction_end_time) if l.auction_end_time else None,
                        })
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(data).encode())
                finally:
                    session.close()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            logger.debug(f"Web: {args[0]}")

    server = HTTPServer((host, port), DashboardHandler)
    logger.info(f"Web dashboard running at http://{host}:{port}")
    print(f"\n  Web dashboard: http://localhost:{port}")
    print(f"  API endpoint:  http://localhost:{port}/api/listings")
    print(f"  Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWeb dashboard stopped.")
        server.server_close()
