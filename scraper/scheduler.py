import logging
from datetime import datetime, timedelta, timezone

from rich.console import Console

from db.database import get_session
from db.models import Listing, PriceResearch, Watchlist
from scraper.maxold import run_scraper
from pricing.ebay import get_ebay_sold_comps
from pricing.claude_ai import research_listing, save_research
from notifications.report import generate_report
from dashboard.terminal import display_dashboard

logger = logging.getLogger(__name__)
console = Console()

# Track state for dashboard display and health monitoring
_state = {
    "last_scrape_time": None,
    "last_report_path": None,
    "consecutive_failures": 0,
    "total_scrapes": 0,
    "total_deals": 0,
}


def _is_watchlist_match(title: str, config: dict) -> bool:
    """Check if a listing title matches any model on the watchlist."""
    watchlist_models = config.get("watchlist", [])
    title_lower = title.lower()
    return any(model.lower() in title_lower for model in watchlist_models)


def _extract_camera_model(title: str) -> str:
    """Extract a likely camera model name from listing title for eBay search."""
    noise_words = {
        "lot", "vintage", "camera", "with", "and", "the", "set",
        "bundle", "collection", "estate", "sale", "box", "case",
        "bag", "strap", "manual", "accessories", "misc", "assorted",
    }
    words = title.split()
    cleaned = [w for w in words if w.lower() not in noise_words]
    return " ".join(cleaned[:4]) if cleaned else title[:50]


def _try_send_webhook(listing, research, config, ending_soon=False):
    """Try to send webhook notifications if configured."""
    try:
        from notifications.webhooks import send_webhooks
        return send_webhooks(listing, research, config, ending_soon=ending_soon)
    except Exception as e:
        logger.warning(f"Webhook send failed: {e}")
        return False


def _try_send_email(listing, research, config):
    """Try to send email alert if email is configured."""
    email_config = config.get("email", {})
    sender = email_config.get("sender_address", "")
    password = email_config.get("app_password", "")

    if not sender or not password or "YOUR_" in sender or "YOUR_" in password:
        return False

    try:
        from notifications.email import send_instant_alert
        return send_instant_alert(listing, research, config)
    except Exception as e:
        logger.warning(f"Email send failed: {e}")
        return False


def _check_ending_soon(config: dict, hours: float = 2.0):
    """Check for deal listings ending within the next N hours and send urgent alerts."""
    session = get_session()
    try:
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours)

        listings = (
            session.query(Listing)
            .filter(
                Listing.auction_end_time.isnot(None),
                Listing.auction_end_time > now,
                Listing.auction_end_time <= cutoff,
                Listing.notified.isnot(True),
            )
            .all()
        )

        urgent_count = 0
        for listing in listings:
            research = (
                session.query(PriceResearch)
                .filter_by(listing_id=listing.id)
                .order_by(PriceResearch.created_at.desc())
                .first()
            )
            if not research or not research.deal_flag:
                continue

            time_left = listing.auction_end_time - now
            hours_left = time_left.total_seconds() / 3600
            console.print(
                f"  [bold red]ENDING SOON:[/bold red] {listing.title} — "
                f"${listing.current_bid:.2f} — {hours_left:.1f}h left"
            )

            # Send urgent alert
            research_dict = {
                "estimated_value": research.estimated_value or 0,
                "max_bid_price": research.max_bid_price or 0,
                "condition_score": research.condition_score or "Fair",
                "condition_notes": research.condition_notes or "",
                "deal_flag": True,
                "summary": f"ENDING SOON ({hours_left:.1f}h left) — {research.condition_notes}",
            }
            _try_send_email(listing, research_dict, config)
            _try_send_webhook(listing, research_dict, config, ending_soon=True)
            urgent_count += 1

        if urgent_count:
            console.print(f"  [red]{urgent_count} deal(s) ending within {hours:.0f} hours[/red]")

    except Exception as e:
        logger.warning(f"Ending-soon check failed: {e}")
    finally:
        session.close()


def run_scrape_pipeline(config: dict, generate_html_report: bool = True):
    """
    Full scraping pipeline:
    1. Scrape Maxsold for new camera listings
    2. For each new listing: get eBay comps, run pricing analysis, save to DB
    3. Send email alert if configured + watchlist match or deal_flag
    4. Generate HTML report
    5. Update dashboard
    """
    console.rule("[bold cyan]Scrape Cycle Starting[/bold cyan]")

    _state["total_scrapes"] += 1

    # Step 1: Scrape
    try:
        new_listings = run_scraper(config)
        _state["consecutive_failures"] = 0
    except Exception as e:
        _state["consecutive_failures"] += 1
        console.print(f"[red]Scraper error: {e}[/red]")
        logger.error(f"Scraper error: {e}", exc_info=True)
        new_listings = []

        if _state["consecutive_failures"] >= 3:
            console.print(
                f"[bold red]WARNING: {_state['consecutive_failures']} consecutive scrape failures. "
                f"Check network or Maxsold availability.[/bold red]"
            )

    console.print(f"[green]Found {len(new_listings)} new listings[/green]")

    deals_found = 0
    alerts_sent = 0

    # Step 2: Research each new listing
    for listing in new_listings:
        title = listing.title if hasattr(listing, "title") else listing.get("title", "")
        listing_id = listing.id if hasattr(listing, "id") else listing.get("id")

        console.print(f"  Researching: [bold]{title}[/bold]")

        # Pre-score condition for condition-aware eBay search
        description = listing.description if hasattr(listing, "description") else ""
        from pricing.claude_ai import _score_condition
        condition_hint, _ = _score_condition(description)

        # Get eBay comps (condition-filtered when possible)
        camera_model = _extract_camera_model(title)
        try:
            ebay_comps = get_ebay_sold_comps(camera_model, config, condition_hint=condition_hint)
            console.print(
                f"    eBay: {ebay_comps['sample_count']} comps, "
                f"avg=${ebay_comps['average_sold']:.2f}"
            )
        except Exception as e:
            console.print(f"    [yellow]eBay lookup failed: {e}[/yellow]")
            logger.warning(f"eBay lookup failed for '{camera_model}': {e}")
            ebay_comps = {
                "average_sold": 0, "min_sold": 0, "max_sold": 0,
                "sample_count": 0, "currency": "CAD", "raw_listings": [],
            }

        # Run pricing analysis
        listing_dict = {
            "title": listing.title if hasattr(listing, "title") else "",
            "description": listing.description if hasattr(listing, "description") else "",
            "current_bid": listing.current_bid if hasattr(listing, "current_bid") else 0,
            "auction_end_time": str(listing.auction_end_time) if hasattr(listing, "auction_end_time") else "",
        }

        try:
            research = research_listing(listing_dict, ebay_comps, config)
            console.print(
                f"    Price: est=${research.get('estimated_value', 0):.2f}, "
                f"condition={research.get('condition_score', '?')}, "
                f"deal={'[green]YES[/green]' if research.get('deal_flag') else 'no'}"
            )
        except Exception as e:
            console.print(f"    [yellow]Pricing failed: {e}[/yellow]")
            logger.warning(f"Pricing failed for '{title}': {e}")
            research = {}

        # Save research to DB
        if research and listing_id:
            try:
                save_research(listing_id, research, ebay_comps)
            except Exception as e:
                console.print(f"    [yellow]Failed to save research: {e}[/yellow]")
                logger.warning(f"Failed to save research: {e}")

        # Step 3: Alerts for watchlist matches or deals (with dedup)
        is_watchlist = _is_watchlist_match(title, config)
        is_deal = research.get("deal_flag", False)
        already_notified = listing.notified if hasattr(listing, "notified") else False

        if is_deal:
            deals_found += 1

        if (is_watchlist or is_deal) and not already_notified:
            reason = []
            if is_watchlist:
                reason.append("watchlist match")
            if is_deal:
                reason.append("deal flagged")
            console.print(f"    [bold green]Alert: {', '.join(reason)}[/bold green]")

            # Try email and/or webhooks
            email_sent = _try_send_email(listing, research, config)
            webhook_sent = _try_send_webhook(listing, research, config)

            # Only mark as notified if at least one notification actually succeeded,
            # OR if no notification channels are configured (to avoid retrying forever)
            has_email_config = bool(config.get("email", {}).get("sender_address", ""))
            has_webhook_config = bool(
                config.get("webhooks", {}).get("slack_url", "")
                or config.get("webhooks", {}).get("discord_url", "")
            )
            no_channels = not has_email_config and not has_webhook_config
            notification_succeeded = email_sent or webhook_sent or no_channels

            if notification_succeeded:
                alerts_sent += 1
                session = get_session()
                try:
                    db_listing = session.query(Listing).get(listing_id)
                    if db_listing:
                        db_listing.notified = True
                        session.commit()
                finally:
                    session.close()
            else:
                console.print(f"    [yellow]Notification failed — will retry next cycle[/yellow]")

    # Step 4: Generate HTML report
    _state["last_scrape_time"] = datetime.now(timezone.utc)

    if generate_html_report and new_listings:
        try:
            report_path = generate_report(hours_back=24, open_browser=False)
            _state["last_report_path"] = report_path
            console.print(f"  [cyan]Report saved: {report_path}[/cyan]")
        except Exception as e:
            console.print(f"  [yellow]Report generation failed: {e}[/yellow]")

    # Summary
    _state["total_deals"] += deals_found
    console.rule("[bold cyan]Scrape Cycle Complete[/bold cyan]")
    console.print(
        f"  Results: {len(new_listings)} new, "
        f"{deals_found} deals, "
        f"{alerts_sent} alerts sent"
    )
    console.print(
        f"  [dim]Session totals: {_state['total_scrapes']} scrapes, "
        f"{_state['total_deals']} deals found[/dim]"
    )

    # Step 5: Check for deals ending soon
    _check_ending_soon(config, hours=2.0)

    # Step 6: Refresh dashboard
    try:
        display_dashboard(
            config,
            last_scrape_time=_state["last_scrape_time"],
            next_scrape_minutes=None,
            last_email_time=None,
        )
    except Exception as e:
        logger.warning(f"Dashboard refresh failed: {e}")


def send_daily_digest_job(config: dict):
    """Wrapper for the daily digest — generates HTML report and optionally sends email."""
    console.print("[bold magenta]Generating daily report...[/bold magenta]")

    # Always generate HTML report
    try:
        report_path = generate_report(hours_back=24, open_browser=False)
        _state["last_report_path"] = report_path
        console.print(f"[green]Report: {report_path}[/green]")
    except Exception as e:
        console.print(f"[red]Report failed: {e}[/red]")
        logger.error(f"Daily report failed: {e}", exc_info=True)

    # Try email digest if configured
    email_config = config.get("email", {})
    sender = email_config.get("sender_address", "")
    password = email_config.get("app_password", "")

    if sender and password and "YOUR_" not in sender and "YOUR_" not in password:
        try:
            from notifications.email import send_daily_digest
            sent = send_daily_digest(config)
            if sent:
                console.print("[green]Email digest sent[/green]")
        except Exception as e:
            logger.warning(f"Email digest failed: {e}")
