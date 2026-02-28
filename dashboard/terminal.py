import logging
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from db.database import get_session
from db.models import Listing, PriceResearch, Watchlist

logger = logging.getLogger(__name__)
console = Console()

# Condition colors for Rich
CONDITION_STYLES = {
    "Excellent": "bold green",
    "Good": "bold yellow",
    "Fair": "bold dark_orange",
    "Parts Only": "bold red",
}


def _build_listings_table(session) -> Table:
    """Build a table of today's new listings."""
    table = Table(
        title="Today's New Listings",
        show_header=True,
        header_style="bold cyan",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Title", style="bold", max_width=40, no_wrap=True)
    table.add_column("Category", style="dim", width=12)
    table.add_column("Bid", justify="right", width=10)
    table.add_column("Est. Value", justify="right", width=10)
    table.add_column("Max Bid", justify="right", width=10)
    table.add_column("Condition", width=12)
    table.add_column("Deal?", justify="center", width=6)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    listings = (
        session.query(Listing)
        .filter(Listing.created_at >= cutoff)
        .order_by(Listing.created_at.desc())
        .all()
    )

    if not listings:
        table.add_row("—", "No new listings today", "", "", "", "", "", "")
        return table

    for i, listing in enumerate(listings, 1):
        research = (
            session.query(PriceResearch)
            .filter_by(listing_id=listing.id)
            .order_by(PriceResearch.created_at.desc())
            .first()
        )

        est_value = f"${research.estimated_value:.2f}" if research else "—"
        max_bid = f"${research.max_bid_price:.2f}" if research else "—"

        condition = research.condition_score if research else "—"
        condition_style = CONDITION_STYLES.get(condition, "")
        condition_text = Text(condition, style=condition_style) if condition_style else Text(condition)

        deal = ""
        if research and research.deal_flag:
            deal = Text("YES", style="bold green")
        elif research:
            deal = Text("no", style="dim")

        table.add_row(
            str(i),
            listing.title[:40],
            listing.category or "—",
            f"${listing.current_bid:.2f}",
            est_value,
            max_bid,
            condition_text,
            deal,
        )

    return table


def _build_watchlist_panel(session) -> Panel:
    """Build a panel showing watched camera models."""
    watchlist_items = session.query(Watchlist).all()

    if not watchlist_items:
        content = Text("No models on watchlist", style="dim")
    else:
        lines = []
        for item in watchlist_items:
            lines.append(f"  • {item.camera_model}")
        content = Text("\n".join(lines))

    return Panel(content, title="[bold magenta]Watchlist[/bold magenta]", border_style="magenta")


def _build_stats_panel(session, last_scrape_time=None, next_scrape_minutes=None, last_email_time=None) -> Panel:
    """Build a panel showing scraping statistics."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    total_today = session.query(Listing).filter(Listing.created_at >= cutoff).count()
    deals_today = (
        session.query(PriceResearch)
        .filter(PriceResearch.created_at >= cutoff, PriceResearch.deal_flag.is_(True))
        .count()
    )
    total_all = session.query(Listing).count()

    lines = [
        f"  Listings today:    {total_today}",
        f"  Deals flagged:     {deals_today}",
        f"  Total in database: {total_all}",
    ]

    if next_scrape_minutes is not None:
        lines.append(f"  Next scrape in:    {next_scrape_minutes} min")
    else:
        lines.append("  Next scrape in:    —")

    content = Text("\n".join(lines))
    return Panel(content, title="[bold blue]Stats[/bold blue]", border_style="blue")


def display_dashboard(config: dict, last_scrape_time=None, next_scrape_minutes=None, last_email_time=None):
    """Display the full terminal dashboard using Rich."""
    console.clear()

    # Header
    header_text = Text()
    header_text.append("  Camera Finder", style="bold white on blue")
    header_text.append(" — Vancouver  ", style="white on blue")
    if last_scrape_time:
        if isinstance(last_scrape_time, datetime):
            time_str = last_scrape_time.strftime("%I:%M %p")
        else:
            time_str = str(last_scrape_time)
        header_text.append(f"  Last scrape: {time_str}", style="dim")

    console.print(Panel(header_text, border_style="blue"))

    session = get_session()
    try:
        # Panel 1: Today's listings table
        listings_table = _build_listings_table(session)
        console.print(listings_table)
        console.print()

        # Panels 2 & 3 side by side
        watchlist_panel = _build_watchlist_panel(session)
        stats_panel = _build_stats_panel(session, last_scrape_time, next_scrape_minutes, last_email_time)

        # Use columns layout
        from rich.columns import Columns
        console.print(Columns([watchlist_panel, stats_panel], equal=True, expand=True))

        # Footer
        footer_parts = []
        if last_email_time:
            if isinstance(last_email_time, datetime):
                email_str = last_email_time.strftime("%b %d at %I:%M %p")
            else:
                email_str = str(last_email_time)
            footer_parts.append(f"Last email sent: {email_str}")
        else:
            footer_parts.append("Last email sent: —")

        console.print()
        console.print(
            Text("  " + " | ".join(footer_parts), style="dim"),
            justify="left",
        )

    finally:
        session.close()
