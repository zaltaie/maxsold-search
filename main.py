#!/usr/bin/env python3
"""
Camera Finder — Vancouver
Automated Maxsold camera auction scraper with pricing research.

Usage:
    python main.py              Start the daily scheduler
    python main.py --scan       Run one scan now and generate a report
    python main.py --report     Generate a report from existing data
    python main.py --dashboard  Show the terminal dashboard
"""

import argparse
import logging
import os
import sys

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
            config["keywords"].setdefault(category, [])
            existing_lower = {k.lower() for k in config["keywords"][category]}
            for kw in kws:
                if kw.lower() not in existing_lower:
                    config["keywords"][category].append(kw)
                    existing_lower.add(kw.lower())
                    total_added += 1
        if total_added:
            console.print(f"  [dim]keywords.txt: {total_added} custom keyword{'s' if total_added != 1 else ''} loaded[/dim]")

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
    parser.add_argument("--dashboard", action="store_true", help="Show terminal dashboard")
    args = parser.parse_args()

    console.print()
    console.print("[bold blue]  Camera Finder — Vancouver  [/bold blue]")
    console.print()

    config = load_config()
    init_db()
    seed_watchlist(config)

    if args.scan:
        cmd_scan(config)
    elif args.report:
        cmd_report(config)
    elif args.dashboard:
        cmd_dashboard(config)
    else:
        cmd_scheduler(config)


if __name__ == "__main__":
    main()
