#!/usr/bin/env python3
"""
Camera Finder — Vancouver
Automated Maxsold camera auction scraper with AI-powered pricing research.
"""

import logging
import os
import sys

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from rich.console import Console

from db.database import init_db, get_session
from db.models import Watchlist
from dashboard.terminal import display_dashboard
from scraper.scheduler import run_scrape_pipeline, send_daily_digest_job

console = Console()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("camera_finder")


def load_config() -> dict:
    """Load configuration from config.yaml."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    if not os.path.exists(config_path):
        console.print(f"[red]Config file not found: {config_path}[/red]")
        console.print("Copy config.yaml.example to config.yaml and fill in your credentials.")
        sys.exit(1)

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    logger.info("Configuration loaded")
    return config


def seed_watchlist(config: dict):
    """Seed the watchlist table from config.yaml if not already populated."""
    session = get_session()
    try:
        existing_count = session.query(Watchlist).count()
        if existing_count > 0:
            logger.info(f"Watchlist already seeded ({existing_count} models)")
            return

        watchlist_models = config.get("watchlist", [])
        for model in watchlist_models:
            entry = Watchlist(
                camera_model=model,
                keywords=[model.lower()],
            )
            session.add(entry)

        session.commit()
        logger.info(f"Watchlist seeded with {len(watchlist_models)} models")
    except Exception as e:
        logger.error(f"Failed to seed watchlist: {e}")
        session.rollback()
    finally:
        session.close()


def main():
    """Main entry point for Camera Finder."""
    console.print()
    console.print("[bold blue]  Camera Finder — Vancouver  [/bold blue]")
    console.print("[dim]  Automated camera auction scraper with AI pricing[/dim]")
    console.print()

    # 1. Load config
    config = load_config()

    # 2. Initialize database
    console.print("[cyan]Initializing database...[/cyan]")
    init_db()

    # 3. Seed watchlist
    console.print("[cyan]Checking watchlist...[/cyan]")
    seed_watchlist(config)

    # 4. Run one immediate scrape cycle
    console.print("[cyan]Running initial scrape...[/cyan]")
    run_scrape_pipeline(config)

    # 5. Display dashboard
    display_dashboard(config)

    # 6. Start scheduler
    scrape_interval = config.get("maxsold", {}).get("scrape_interval_minutes", 30)
    digest_hour = config.get("email", {}).get("digest_hour", 8)

    console.print()
    console.print(f"[green]Scheduler starting:[/green]")
    console.print(f"  • Scraping every {scrape_interval} minutes")
    console.print(f"  • Daily digest at {digest_hour}:00")
    console.print(f"  • Press Ctrl+C to stop")
    console.print()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_scrape_pipeline,
        "interval",
        minutes=scrape_interval,
        args=[config],
        id="scrape_pipeline",
    )
    scheduler.add_job(
        send_daily_digest_job,
        "cron",
        hour=digest_hour,
        minute=0,
        args=[config],
        id="daily_digest",
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        console.print("\n[yellow]Camera Finder stopped.[/yellow]")


if __name__ == "__main__":
    main()
