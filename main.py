#!/usr/bin/env python3
"""
Camera Finder — Vancouver
Automated Maxsold camera auction scraper with pricing research.

Usage:
    python main.py                       Start the daily scheduler
    python main.py --scan                Run one scan now and generate a report
    python main.py --report              Generate a report from existing data
    python main.py --export              Export listings to CSV
    python main.py --dashboard           Show the terminal dashboard
    python main.py --web                 Start web dashboard (http://localhost:5050)
    python main.py --watch "Nikon F3"    Add a model to the watchlist
    python main.py --unwatch "Nikon F3"  Remove a model from the watchlist
    python main.py --watchlist           Show all watched models
"""

import argparse
import logging
import os
import sys
from datetime import datetime

import yaml
from rich.console import Console

from db.database import init_db, get_session
from db.models import Watchlist

console = Console()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("camera_finder")


def load_keywords_file() -> dict:
    """
    Load extra keywords from keywords.txt (same directory as main.py).

    Format: one keyword per line, # comments, blank lines ignored.
    Optional [category] section headers route keywords into named categories.
    Keywords before any header go into "custom".

    Returns a dict of {category: [keywords]}, or {} if the file doesn't exist.
    """
    keywords_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keywords.txt")
    if not os.path.exists(keywords_path):
        return {}

    result: dict = {}
    current_category = "custom"

    with open(keywords_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_category = line[1:-1].strip().lower()
                continue
            result.setdefault(current_category, [])
            result[current_category].append(line)

    return result


def load_config() -> dict:
    """Load configuration from config.yaml, then merge in keywords.txt."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    if not os.path.exists(config_path):
        console.print(f"[red]Config file not found: {config_path}[/red]")
        sys.exit(1)

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Merge keywords.txt into config["keywords"]
    extra = load_keywords_file()
    if extra:
        total_added = 0
        for category, kws in extra.items():
            if category == "exclude":
                # Exclude keywords stored separately
                config.setdefault("exclude_keywords", [])
                for kw in kws:
                    if kw.lower() not in {e.lower() for e in config["exclude_keywords"]}:
                        config["exclude_keywords"].append(kw)
                        total_added += 1
                continue
            config["keywords"].setdefault(category, [])
            existing_lower = {k.lower() for k in config["keywords"][category]}
            for kw in kws:
                if kw.lower() not in existing_lower:
                    config["keywords"][category].append(kw)
                    existing_lower.add(kw.lower())
                    total_added += 1
        exclude_count = len(config.get("exclude_keywords", []))
        if total_added or exclude_count:
            parts = []
            if total_added:
                parts.append(f"{total_added} keyword{'s' if total_added != 1 else ''}")
            if exclude_count:
                parts.append(f"{exclude_count} exclude filter{'s' if exclude_count != 1 else ''}")
            console.print(f"  [dim]keywords.txt: {', '.join(parts)} loaded[/dim]")

    return config


def seed_watchlist(config: dict):
    """Seed the watchlist table from config.yaml if not already populated."""
    session = get_session()
    try:
        existing_count = session.query(Watchlist).count()
        if existing_count > 0:
            return

        watchlist_models = config.get("watchlist", [])
        for model in watchlist_models:
            session.add(Watchlist(camera_model=model, keywords=[model.lower()]))

        session.commit()
        logger.info(f"Watchlist seeded with {len(watchlist_models)} models")
    except Exception as e:
        logger.error(f"Failed to seed watchlist: {e}")
        session.rollback()
    finally:
        session.close()


def cmd_scan(config: dict):
    """Run a single scan, price listings, and open an HTML report."""
    from scraper.scheduler import run_scrape_pipeline
    from notifications.report import generate_report

    console.print("[cyan]Running scan...[/cyan]")
    run_scrape_pipeline(config, generate_html_report=False)

    console.print("[cyan]Generating report...[/cyan]")
    report_path = generate_report(hours_back=24, open_browser=True)
    console.print(f"[green]Report: {report_path}[/green]")


def cmd_report(config: dict):
    """Generate a report from existing data in the database."""
    from notifications.report import generate_report

    report_path = generate_report(hours_back=24, open_browser=True)
    console.print(f"[green]Report: {report_path}[/green]")


def cmd_export(config: dict, hours_back: int = 24):
    """Export listings to CSV."""
    import csv
    from datetime import timedelta, timezone
    from db.models import Listing, PriceResearch

    session = get_session()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        listings = (
            session.query(Listing)
            .filter(Listing.created_at >= cutoff)
            .order_by(Listing.created_at.desc())
            .all()
        )

        if not listings:
            console.print("[yellow]No listings found to export.[/yellow]")
            return

        export_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
        os.makedirs(export_dir, exist_ok=True)

        filename = f"export_{datetime.now().strftime('%Y-%m-%d_%H%M')}.csv"
        filepath = os.path.join(export_dir, filename)

        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Title", "Category", "Current Bid", "Est. Value", "Max Bid",
                "FB Marketplace", "Condition", "Deal?", "Auction Ends",
                "URL", "Condition Notes",
            ])
            for listing in listings:
                research = (
                    session.query(PriceResearch)
                    .filter_by(listing_id=listing.id)
                    .order_by(PriceResearch.created_at.desc())
                    .first()
                )
                end_str = listing.auction_end_time.strftime("%Y-%m-%d %H:%M") if listing.auction_end_time else ""
                writer.writerow([
                    listing.title,
                    listing.category or "",
                    f"{listing.current_bid:.2f}",
                    f"{research.estimated_value:.2f}" if research else "",
                    f"{research.max_bid_price:.2f}" if research else "",
                    f"{research.fb_marketplace_ceiling:.2f}" if research else "",
                    research.condition_score if research else "",
                    "YES" if research and research.deal_flag else "no",
                    end_str,
                    listing.maxsold_url,
                    research.condition_notes if research else "",
                ])

        console.print(f"[green]Exported {len(listings)} listings to {filepath}[/green]")

    finally:
        session.close()


def cmd_watch(model: str):
    """Add a camera model to the watchlist."""
    session = get_session()
    try:
        existing = session.query(Watchlist).filter_by(camera_model=model).first()
        if existing:
            console.print(f"[yellow]'{model}' is already on the watchlist.[/yellow]")
            return
        session.add(Watchlist(camera_model=model, keywords=[model.lower()]))
        session.commit()
        console.print(f"[green]Added '{model}' to watchlist.[/green]")
    except Exception as e:
        console.print(f"[red]Failed: {e}[/red]")
        session.rollback()
    finally:
        session.close()


def cmd_unwatch(model: str):
    """Remove a camera model from the watchlist."""
    session = get_session()
    try:
        item = session.query(Watchlist).filter_by(camera_model=model).first()
        if not item:
            # Case-insensitive fallback
            item = session.query(Watchlist).filter(
                Watchlist.camera_model.ilike(model)
            ).first()
        if not item:
            console.print(f"[yellow]'{model}' not found on the watchlist.[/yellow]")
            return
        name = item.camera_model
        session.delete(item)
        session.commit()
        console.print(f"[green]Removed '{name}' from watchlist.[/green]")
    except Exception as e:
        console.print(f"[red]Failed: {e}[/red]")
        session.rollback()
    finally:
        session.close()


def cmd_watchlist():
    """Show all watched models."""
    session = get_session()
    try:
        items = session.query(Watchlist).all()
        if not items:
            console.print("[dim]Watchlist is empty.[/dim]")
            return
        console.print(f"[bold]Watchlist ({len(items)} models):[/bold]")
        for item in items:
            console.print(f"  - {item.camera_model}")
    finally:
        session.close()


def cmd_dashboard(config: dict):
    """Show the terminal dashboard."""
    from dashboard.terminal import display_dashboard
    display_dashboard(config)


def cmd_scheduler(config: dict):
    """Start the daily scheduler."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from scraper.scheduler import run_scrape_pipeline, send_daily_digest_job

    scan_time = config.get("maxsold", {}).get("scan_time", "08:00")

    if not scan_time:
        console.print("[yellow]No scan_time configured. Use --scan for manual scans.[/yellow]")
        return

    # Parse scan time
    try:
        hour, minute = map(int, scan_time.split(":"))
    except (ValueError, AttributeError):
        hour, minute = 8, 0

    # Run one immediate scan
    console.print("[cyan]Running initial scan...[/cyan]")
    run_scrape_pipeline(config)

    console.print()
    console.print(f"[green]Scheduler running:[/green]")
    console.print(f"  Daily scan at {hour:02d}:{minute:02d}")
    console.print(f"  Press Ctrl+C to stop")
    console.print(f"  Or run [bold]python main.py --scan[/bold] anytime for on-demand scans")
    console.print()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_scrape_pipeline,
        "cron",
        hour=hour,
        minute=minute,
        args=[config],
        id="daily_scan",
    )
    scheduler.add_job(
        send_daily_digest_job,
        "cron",
        hour=hour,
        minute=minute + 5 if minute < 55 else minute,
        args=[config],
        id="daily_report",
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        console.print("\n[yellow]Camera Finder stopped.[/yellow]")


def main():
    parser = argparse.ArgumentParser(
        description="Camera Finder — Maxsold auction scraper for Vancouver camera deals",
    )
    parser.add_argument("--scan", action="store_true", help="Run one scan now and open the report")
    parser.add_argument("--report", action="store_true", help="Generate report from existing data")
    parser.add_argument("--export", action="store_true", help="Export listings to CSV")
    parser.add_argument("--dashboard", action="store_true", help="Show terminal dashboard")
    parser.add_argument("--web", action="store_true", help="Start web dashboard (http://localhost:5050)")
    parser.add_argument("--watch", metavar="MODEL", help="Add a camera model to the watchlist")
    parser.add_argument("--unwatch", metavar="MODEL", help="Remove a camera model from the watchlist")
    parser.add_argument("--watchlist", action="store_true", help="Show all watched models")
    args = parser.parse_args()

    console.print()
    console.print("[bold blue]  Camera Finder — Vancouver  [/bold blue]")
    console.print()

    config = load_config()
    init_db()
    seed_watchlist(config)

    if args.watch:
        cmd_watch(args.watch)
    elif args.unwatch:
        cmd_unwatch(args.unwatch)
    elif args.watchlist:
        cmd_watchlist()
    elif args.scan:
        cmd_scan(config)
    elif args.report:
        cmd_report(config)
    elif args.export:
        cmd_export(config)
    elif args.dashboard:
        cmd_dashboard(config)
    elif args.web:
        from dashboard.web import run_web_dashboard
        run_web_dashboard()
    else:
        cmd_scheduler(config)


if __name__ == "__main__":
    main()
