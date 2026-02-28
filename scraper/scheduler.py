import logging
from datetime import datetime, timezone

from rich.console import Console

from db.database import get_session
from db.models import Listing, Watchlist
from scraper.maxold import run_scraper
from pricing.ebay import get_ebay_sold_comps
from pricing.claude_ai import research_listing, save_research
from notifications.email import send_instant_alert, send_daily_digest
from dashboard.terminal import display_dashboard

logger = logging.getLogger(__name__)
console = Console()

# Track state for dashboard display
_state = {
    "last_scrape_time": None,
    "last_email_time": None,
}


def _is_watchlist_match(title: str, config: dict) -> bool:
    """Check if a listing title matches any model on the watchlist."""
    watchlist_models = config.get("watchlist", [])
    title_lower = title.lower()
    return any(model.lower() in title_lower for model in watchlist_models)


def _extract_camera_model(title: str) -> str:
    """Extract a likely camera model name from listing title for eBay search."""
    # Remove common non-model words
    noise_words = {
        "lot", "vintage", "camera", "with", "and", "the", "set",
        "bundle", "collection", "estate", "sale", "box", "case",
        "bag", "strap", "manual", "accessories", "misc", "assorted",
    }
    words = title.split()
    cleaned = [w for w in words if w.lower() not in noise_words]
    # Return first 4 meaningful words as the search query
    return " ".join(cleaned[:4]) if cleaned else title[:50]


def run_scrape_pipeline(config: dict):
    """
    Full scraping pipeline:
    1. Scrape Maxsold for new camera listings
    2. For each new listing: get eBay comps, run Claude research, save to DB
    3. Send instant alert if watchlist match or deal_flag
    4. Update dashboard
    """
    console.rule("[bold cyan]Scrape Cycle Starting[/bold cyan]")

    # Step 1: Scrape
    try:
        new_listings = run_scraper(config)
    except Exception as e:
        console.print(f"[red]Scraper error: {e}[/red]")
        logger.error(f"Scraper error: {e}", exc_info=True)
        new_listings = []

    console.print(f"[green]Found {len(new_listings)} new listings[/green]")

    emails_sent = 0
    deals_found = 0

    # Step 2: Research each new listing
    for listing in new_listings:
        title = listing.title if hasattr(listing, "title") else listing.get("title", "")
        listing_id = listing.id if hasattr(listing, "id") else listing.get("id")

        console.print(f"  Researching: [bold]{title}[/bold]")

        # Get eBay comps
        camera_model = _extract_camera_model(title)
        try:
            ebay_comps = get_ebay_sold_comps(camera_model, config)
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

        # Run Claude AI research
        listing_dict = {
            "title": listing.title if hasattr(listing, "title") else "",
            "description": listing.description if hasattr(listing, "description") else "",
            "current_bid": listing.current_bid if hasattr(listing, "current_bid") else 0,
            "auction_end_time": str(listing.auction_end_time) if hasattr(listing, "auction_end_time") else "",
        }

        try:
            research = research_listing(listing_dict, ebay_comps, config)
            console.print(
                f"    AI: est=${research.get('estimated_value', 0):.2f}, "
                f"condition={research.get('condition_score', '?')}, "
                f"deal={'[green]YES[/green]' if research.get('deal_flag') else 'no'}"
            )
        except Exception as e:
            console.print(f"    [yellow]AI research failed: {e}[/yellow]")
            logger.warning(f"AI research failed for '{title}': {e}")
            research = {}

        # Save research to DB
        if research and listing_id:
            try:
                save_research(listing_id, research, ebay_comps)
            except Exception as e:
                console.print(f"    [yellow]Failed to save research: {e}[/yellow]")
                logger.warning(f"Failed to save research: {e}")

        # Step 3: Send instant alert if warranted
        is_watchlist = _is_watchlist_match(title, config)
        is_deal = research.get("deal_flag", False)

        if is_deal:
            deals_found += 1

        if is_watchlist or is_deal:
            reason = []
            if is_watchlist:
                reason.append("watchlist match")
            if is_deal:
                reason.append("deal flagged")

            console.print(f"    [bold green]Alert triggered ({', '.join(reason)})[/bold green]")

            try:
                sent = send_instant_alert(listing, research, config)
                if sent:
                    emails_sent += 1
                    _state["last_email_time"] = datetime.now(timezone.utc)

                    # Mark listing as notified
                    session = get_session()
                    try:
                        db_listing = session.query(Listing).get(listing_id)
                        if db_listing:
                            db_listing.notified = True
                            session.commit()
                    finally:
                        session.close()
            except Exception as e:
                console.print(f"    [red]Email failed: {e}[/red]")
                logger.error(f"Email failed: {e}")

    # Summary
    _state["last_scrape_time"] = datetime.now(timezone.utc)
    console.rule("[bold cyan]Scrape Cycle Complete[/bold cyan]")
    console.print(
        f"  Results: {len(new_listings)} new, "
        f"{deals_found} deals, "
        f"{emails_sent} emails sent"
    )

    # Step 4: Refresh dashboard
    try:
        scrape_interval = config.get("maxsold", {}).get("scrape_interval_minutes", 30)
        display_dashboard(
            config,
            last_scrape_time=_state["last_scrape_time"],
            next_scrape_minutes=scrape_interval,
            last_email_time=_state["last_email_time"],
        )
    except Exception as e:
        logger.warning(f"Dashboard refresh failed: {e}")


def send_daily_digest_job(config: dict):
    """Wrapper for the daily digest email job."""
    console.print("[bold magenta]Sending daily digest...[/bold magenta]")
    try:
        sent = send_daily_digest(config)
        if sent:
            _state["last_email_time"] = datetime.now(timezone.utc)
            console.print("[green]Daily digest sent[/green]")
        else:
            console.print("[yellow]Daily digest skipped (no new listings or email not configured)[/yellow]")
    except Exception as e:
        console.print(f"[red]Daily digest failed: {e}[/red]")
        logger.error(f"Daily digest failed: {e}", exc_info=True)
