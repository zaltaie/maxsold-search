import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from db.database import get_session
from db.models import Listing, PriceResearch

logger = logging.getLogger(__name__)

# Color coding for condition scores
CONDITION_COLORS = {
    "Excellent": "#28a745",
    "Good": "#ffc107",
    "Fair": "#fd7e14",
    "Parts Only": "#dc3545",
}


def _send_email(subject: str, html_body: str, config: dict) -> bool:
    """Send an HTML email via Gmail SMTP."""
    email_config = config.get("email", {})
    sender = email_config.get("sender_address", "")
    recipient = email_config.get("recipient_address", sender)
    password = email_config.get("app_password", "")
    smtp_host = email_config.get("smtp_host", "smtp.gmail.com")
    smtp_port = email_config.get("smtp_port", 587)

    if not sender or not password or "YOUR_" in sender or "YOUR_" in password:
        logger.warning("Email not configured, skipping send")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        logger.info(f"Email sent: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def send_instant_alert(listing, research: dict, config: dict) -> bool:
    """
    Send an instant alert email for a new listing that matches watchlist or has deal_flag=True.

    Args:
        listing: Listing object or dict with listing data
        research: dict with pricing research data
        config: full config dict
    """
    title = listing.title if hasattr(listing, "title") else listing.get("title", "Unknown")
    current_bid = listing.current_bid if hasattr(listing, "current_bid") else listing.get("current_bid", 0)
    maxsold_url = listing.maxsold_url if hasattr(listing, "maxsold_url") else listing.get("maxsold_url", "#")
    description = listing.description if hasattr(listing, "description") else listing.get("description", "")
    photo_urls = listing.photo_urls if hasattr(listing, "photo_urls") else listing.get("photo_urls", [])
    end_time = listing.auction_end_time if hasattr(listing, "auction_end_time") else listing.get("auction_end_time")

    estimated_value = research.get("estimated_value", 0)
    max_bid = research.get("max_bid_price", 0)
    condition = research.get("condition_score", "Fair")
    condition_notes = research.get("condition_notes", "")
    summary = research.get("summary", "")
    deal_flag = research.get("deal_flag", False)

    condition_color = CONDITION_COLORS.get(condition, "#6c757d")

    # Get first photo URL
    photo_html = ""
    if photo_urls and isinstance(photo_urls, list) and photo_urls:
        photo_html = f'<img src="{photo_urls[0]}" alt="{title}" style="max-width:100%;height:auto;border-radius:8px;margin:12px 0;" />'

    end_time_str = ""
    if end_time:
        if isinstance(end_time, datetime):
            end_time_str = end_time.strftime("%b %d, %Y at %I:%M %p %Z")
        else:
            end_time_str = str(end_time)

    deal_badge = ""
    if deal_flag:
        deal_badge = '<span style="background:#28a745;color:#fff;padding:4px 12px;border-radius:12px;font-weight:bold;font-size:14px;">DEAL</span>'

    subject = f"[CAMERA ALERT] {title} — Bid ${current_bid:.2f} / Est. ${estimated_value:.2f}"

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:600px;margin:0 auto;padding:16px;background:#f8f9fa;">
  <div style="background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,0.1);">

    <h1 style="font-size:22px;margin:0 0 8px 0;color:#212529;">{title}</h1>
    {deal_badge}

    {photo_html}

    <table style="width:100%;border-collapse:collapse;margin:16px 0;">
      <tr>
        <td style="padding:8px 12px;background:#f1f3f5;border-radius:6px;width:50%;">
          <div style="font-size:12px;color:#868e96;text-transform:uppercase;">Current Bid</div>
          <div style="font-size:24px;font-weight:bold;color:#212529;">${current_bid:.2f} <span style="font-size:12px;color:#868e96;">CAD</span></div>
        </td>
        <td style="padding:8px 12px;background:#f1f3f5;border-radius:6px;width:50%;">
          <div style="font-size:12px;color:#868e96;text-transform:uppercase;">Estimated Value</div>
          <div style="font-size:24px;font-weight:bold;color:#28a745;">${estimated_value:.2f} <span style="font-size:12px;color:#868e96;">CAD</span></div>
        </td>
      </tr>
    </table>

    <table style="width:100%;border-collapse:collapse;margin:8px 0;">
      <tr>
        <td style="padding:6px 0;color:#495057;"><strong>Max Bid Recommendation:</strong></td>
        <td style="padding:6px 0;color:#212529;font-weight:bold;">${max_bid:.2f} CAD</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#495057;"><strong>Condition:</strong></td>
        <td style="padding:6px 0;">
          <span style="background:{condition_color};color:#fff;padding:3px 10px;border-radius:10px;font-size:13px;font-weight:bold;">{condition}</span>
        </td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#495057;"><strong>Condition Notes:</strong></td>
        <td style="padding:6px 0;color:#495057;font-size:14px;">{condition_notes}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#495057;"><strong>Auction Ends:</strong></td>
        <td style="padding:6px 0;color:#dc3545;font-weight:bold;">{end_time_str}</td>
      </tr>
    </table>

    <div style="background:#e7f5ff;border-left:4px solid #339af0;padding:12px 16px;margin:16px 0;border-radius:0 6px 6px 0;">
      <div style="font-size:12px;color:#339af0;text-transform:uppercase;margin-bottom:4px;">Analysis</div>
      <div style="color:#212529;font-size:14px;">{summary}</div>
    </div>

    <a href="{maxsold_url}" style="display:block;text-align:center;background:#339af0;color:#fff;padding:14px 24px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:16px;margin-top:16px;">
      View on Maxsold
    </a>

  </div>
  <div style="text-align:center;padding:16px;color:#adb5bd;font-size:12px;">
    Camera Finder — Vancouver | Automated alert
  </div>
</body>
</html>"""

    return _send_email(subject, html_body, config)


def send_daily_digest(config: dict) -> bool:
    """
    Send a daily digest email with all listings found in the last 24 hours.
    """
    session = get_session()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        listings = (
            session.query(Listing)
            .filter(Listing.created_at >= cutoff)
            .order_by(Listing.created_at.desc())
            .all()
        )

        if not listings:
            logger.info("No new listings in last 24h, skipping digest")
            return False

        today_str = datetime.now().strftime("%b %d, %Y")
        subject = f"[Camera Finder] Daily Digest — {today_str} — {len(listings)} new listings"

        # Build table rows
        rows_html = ""
        for listing in listings:
            # Get associated research
            research = (
                session.query(PriceResearch)
                .filter_by(listing_id=listing.id)
                .order_by(PriceResearch.created_at.desc())
                .first()
            )

            # Photo thumbnail
            photo_html = ""
            if listing.photo_urls and isinstance(listing.photo_urls, list) and listing.photo_urls:
                photo_html = f'<img src="{listing.photo_urls[0]}" alt="" style="width:60px;height:60px;object-fit:cover;border-radius:4px;" />'

            est_value = f"${research.estimated_value:.2f}" if research else "—"
            max_bid = f"${research.max_bid_price:.2f}" if research else "—"
            condition = research.condition_score if research else "—"
            condition_color = CONDITION_COLORS.get(condition, "#6c757d")

            rows_html += f"""<tr style="border-bottom:1px solid #e9ecef;">
        <td style="padding:10px 8px;text-align:center;">{photo_html}</td>
        <td style="padding:10px 8px;">
          <a href="{listing.maxsold_url}" style="color:#339af0;text-decoration:none;font-weight:bold;font-size:14px;">{listing.title}</a>
        </td>
        <td style="padding:10px 8px;text-align:right;font-weight:bold;">${listing.current_bid:.2f}</td>
        <td style="padding:10px 8px;text-align:right;color:#28a745;">{est_value}</td>
        <td style="padding:10px 8px;text-align:right;">{max_bid}</td>
        <td style="padding:10px 8px;text-align:center;">
          <span style="background:{condition_color};color:#fff;padding:2px 8px;border-radius:8px;font-size:11px;">{condition}</span>
        </td>
        <td style="padding:10px 8px;text-align:center;">
          <a href="{listing.maxsold_url}" style="color:#339af0;text-decoration:none;font-size:13px;">View</a>
        </td>
      </tr>"""

        html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:700px;margin:0 auto;padding:16px;background:#f8f9fa;">
  <div style="background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,0.1);">

    <h1 style="font-size:20px;margin:0 0 4px 0;color:#212529;">Camera Finder — Daily Digest</h1>
    <p style="color:#868e96;margin:0 0 20px 0;">{today_str} — {len(listings)} new listing{"s" if len(listings) != 1 else ""}</p>

    <table style="width:100%;border-collapse:collapse;">
      <thead>
        <tr style="background:#f1f3f5;">
          <th style="padding:10px 8px;text-align:center;font-size:12px;color:#868e96;text-transform:uppercase;">Photo</th>
          <th style="padding:10px 8px;text-align:left;font-size:12px;color:#868e96;text-transform:uppercase;">Title</th>
          <th style="padding:10px 8px;text-align:right;font-size:12px;color:#868e96;text-transform:uppercase;">Bid</th>
          <th style="padding:10px 8px;text-align:right;font-size:12px;color:#868e96;text-transform:uppercase;">Est. Value</th>
          <th style="padding:10px 8px;text-align:right;font-size:12px;color:#868e96;text-transform:uppercase;">Max Bid</th>
          <th style="padding:10px 8px;text-align:center;font-size:12px;color:#868e96;text-transform:uppercase;">Condition</th>
          <th style="padding:10px 8px;text-align:center;font-size:12px;color:#868e96;text-transform:uppercase;">Link</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>

  </div>
  <div style="text-align:center;padding:16px;color:#adb5bd;font-size:12px;">
    Camera Finder — Vancouver | Daily digest
  </div>
</body>
</html>"""

        return _send_email(subject, html_body, config)

    except Exception as e:
        logger.error(f"Failed to generate daily digest: {e}")
        return False
    finally:
        session.close()
