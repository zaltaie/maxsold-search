import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from playwright.async_api import async_playwright

from db.database import get_session
from db.models import Listing, BidHistory

logger = logging.getLogger(__name__)

# Vancouver coordinates for Algolia geosearch
VANCOUVER_LAT_LNG = "49.2827,-123.1207"
SEARCH_RADIUS_METERS = 100000  # 100 km

# Algolia endpoint template
ALGOLIA_BASE = "https://bwhj2cu1lu-dsn.algolia.net/1/indexes"
ALGOLIA_AGENT = "Algolia%20for%20JavaScript%20(4.11.0)%3B%20Browser"

# Maxsold internal API endpoints
MAXSOLD_API_ITEMS = "https://maxsold.maxsold.com/api/getitems"
MAXSOLD_API_ITEM_DATA = "https://maxsold.maxsold.com/api/itemdata"


def _extract_algolia_tokens(js_text: str) -> dict:
    """Extract Algolia API key and app ID from Maxsold's JS bundle."""
    tokens = {}
    for key in ["algoliaApplicationId", "algoliaSearchAPIKey"]:
        pattern = f'{key}:"'
        start = js_text.find(pattern)
        if start == -1:
            # Try alternate patterns (bundled JS may use different formats)
            alt_pattern = f'"{key}":"'
            start = js_text.find(alt_pattern)
            if start == -1:
                continue
            start += len(alt_pattern)
        else:
            start += len(pattern)
        end = start + js_text[start:].find('"')
        tokens[key] = js_text[start:end]
    return tokens


async def _get_algolia_tokens_via_playwright() -> dict:
    """Use Playwright to load Maxsold and extract Algolia credentials from the JS bundle."""
    tokens = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        js_urls = []

        # Intercept JS bundle requests to find the main bundle
        async def handle_response(response):
            url = response.url
            if "main." in url and url.endswith(".js"):
                js_urls.append(url)

        page.on("response", handle_response)

        try:
            await page.goto("https://maxsold.com", wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"Page load timeout (non-fatal): {e}")

        # If we captured a JS bundle URL, fetch and extract tokens
        if js_urls:
            for js_url in js_urls:
                try:
                    resp = await page.evaluate(f"""
                        async () => {{
                            const resp = await fetch("{js_url}");
                            return await resp.text();
                        }}
                    """)
                    tokens = _extract_algolia_tokens(resp)
                    if tokens.get("algoliaSearchAPIKey"):
                        break
                except Exception as e:
                    logger.warning(f"Failed to fetch JS bundle {js_url}: {e}")

        # Fallback: look for tokens in page source scripts
        if not tokens.get("algoliaSearchAPIKey"):
            scripts = await page.query_selector_all("script[src]")
            for script in scripts:
                src = await script.get_attribute("src")
                if src and "main." in src:
                    try:
                        full_url = src if src.startswith("http") else f"https://maxsold.com{src}"
                        resp = await page.evaluate(f"""
                            async () => {{
                                const resp = await fetch("{full_url}");
                                return await resp.text();
                            }}
                        """)
                        tokens = _extract_algolia_tokens(resp)
                        if tokens.get("algoliaSearchAPIKey"):
                            break
                    except Exception:
                        continue

        await browser.close()

    return tokens


def _get_algolia_tokens_direct() -> dict:
    """Try to extract Algolia tokens by fetching the Maxsold JS bundle directly."""
    try:
        # First, get the main page to find the JS bundle filename
        resp = requests.get("https://maxsold.com", timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        # Find main.{hash}.js pattern in the HTML
        matches = re.findall(r'(?:src|href)=["\']([^"\']*main\.[a-f0-9]+\.js)["\']', resp.text)
        if not matches:
            return {}

        js_url = matches[0]
        if not js_url.startswith("http"):
            js_url = f"https://maxsold.com{js_url}" if js_url.startswith("/") else f"https://maxsold.com/{js_url}"

        js_resp = requests.get(js_url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        return _extract_algolia_tokens(js_resp.text)
    except Exception as e:
        logger.warning(f"Direct token extraction failed: {e}")
        return {}


def get_algolia_tokens() -> dict:
    """Get Algolia tokens, trying direct method first then Playwright fallback."""
    tokens = _get_algolia_tokens_direct()
    if tokens.get("algoliaSearchAPIKey") and tokens.get("algoliaApplicationId"):
        logger.info("Algolia tokens extracted via direct HTTP")
        return tokens

    logger.info("Falling back to Playwright for token extraction")
    tokens = asyncio.run(_get_algolia_tokens_via_playwright())
    if tokens.get("algoliaSearchAPIKey"):
        logger.info("Algolia tokens extracted via Playwright")
    else:
        logger.error("Failed to extract Algolia tokens")
    return tokens


def search_auctions(tokens: dict, page: int = 0) -> dict:
    """Search for active auctions near Vancouver using the Algolia API."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-algolia-api-key": tokens["algoliaSearchAPIKey"],
        "x-algolia-application-id": tokens["algoliaApplicationId"],
        "Content-Type": "application/json",
    }

    now = int(time.time())
    # end_date > (now - 900) means auction hasn't ended yet (with 15-min buffer)
    end_threshold = now - 900

    data = json.dumps({
        "query": "",
        "filters": f"start_date <= {now} AND end_date > {end_threshold}",
        "facetFilters": ["auction_phase:-cancelledAuction"],
        "hitsPerPage": 100,
        "page": page,
        "aroundLatLng": VANCOUVER_LAT_LNG,
        "aroundLatLngViaIP": False,
        "aroundRadius": SEARCH_RADIUS_METERS,
    })

    url = f"{ALGOLIA_BASE}/hpauction/query?x-algolia-agent={ALGOLIA_AGENT}"
    resp = requests.post(url, headers=headers, data=data, timeout=15)
    resp.raise_for_status()
    return resp.json()


def search_items(tokens: dict, query: str = "", page: int = 0) -> dict:
    """Search for individual items across auctions using the Algolia API."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-algolia-api-key": tokens["algoliaSearchAPIKey"],
        "x-algolia-application-id": tokens["algoliaApplicationId"],
        "Content-Type": "application/json",
    }

    now = int(time.time())
    end_threshold = now - 900

    data = json.dumps({
        "query": query,
        "filters": f"start_date <= {now} AND end_date > {end_threshold}",
        "facetFilters": ["auction_phase:-cancelledAuction"],
        "hitsPerPage": 100,
        "page": page,
        "aroundLatLng": VANCOUVER_LAT_LNG,
        "aroundLatLngViaIP": False,
        "aroundRadius": SEARCH_RADIUS_METERS,
    })

    url = f"{ALGOLIA_BASE}/hpitem/query?x-algolia-agent={ALGOLIA_AGENT}"
    resp = requests.post(url, headers=headers, data=data, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_auction_items(auction_id: int, page_id: int = 1) -> dict:
    """Get all items for a specific auction via Maxsold's internal API."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    data = {
        "auction_id": str(auction_id),
        "filters[page]": str(page_id),
        "item_type": "itemlist",
        "lotnum": "0",
        "close_groups": "",
        "show_closed": "closed",
        "perpetual": "",
    }
    resp = requests.post(MAXSOLD_API_ITEMS, headers=headers, data=data, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_item_detail(item_id: int) -> dict:
    """Get detailed data for a single auction item via Maxsold's internal API."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    data = {"item_id": str(item_id)}
    resp = requests.post(MAXSOLD_API_ITEM_DATA, headers=headers, data=data, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _match_keywords(text: str, keywords_config: dict, exclude_keywords: list = None) -> Optional[str]:
    """Check if text matches any keyword category. Returns category name or None.
    Skips listings that match any exclude keyword."""
    text_lower = text.lower()

    # Check exclusions first
    if exclude_keywords:
        for keyword in exclude_keywords:
            if keyword.lower() in text_lower:
                return None

    for category, keyword_list in keywords_config.items():
        for keyword in keyword_list:
            if keyword.lower() in text_lower:
                return category
    return None


def _build_item_url(auction_id, item_id) -> str:
    """Construct a Maxsold item URL."""
    return f"https://maxsold.com/auction/{auction_id}/item/{item_id}"


def _parse_photo_urls(item_data: dict) -> list:
    """Extract photo URLs from item data."""
    photos = []
    # Item data may contain image fields in various formats
    if "images" in item_data and isinstance(item_data["images"], list):
        for img in item_data["images"]:
            if isinstance(img, str):
                photos.append(img)
            elif isinstance(img, dict) and "url" in img:
                photos.append(img["url"])
    if "image" in item_data and isinstance(item_data["image"], str):
        photos.append(item_data["image"])
    # Check for photo_url or thumbnail fields
    for key in ["photo_url", "thumbnail", "main_image"]:
        if key in item_data and item_data[key]:
            photos.append(item_data[key])
    return photos


def _parse_auction_end_time(item_data: dict) -> Optional[datetime]:
    """Parse auction end time from item data."""
    for key in ["end_date", "close_date", "auction_end_time", "end_time"]:
        if key in item_data:
            val = item_data[key]
            if isinstance(val, (int, float)):
                return datetime.fromtimestamp(val, tz=timezone.utc)
            if isinstance(val, str):
                try:
                    return datetime.fromisoformat(val.replace("Z", "+00:00"))
                except ValueError:
                    try:
                        return datetime.fromtimestamp(int(val), tz=timezone.utc)
                    except (ValueError, OSError):
                        continue
    return None


def scrape_maxsold(config: dict) -> list:
    """
    Main scraping function. Searches Maxsold for camera-related auctions
    in the Vancouver area, saves new listings to DB, and records bid history.

    Returns a list of newly found Listing objects.
    """
    keywords_config = config.get("keywords", {})
    exclude_keywords = config.get("exclude_keywords", [])

    # Step 1: Get Algolia tokens
    logger.info("Extracting Algolia API tokens...")
    tokens = get_algolia_tokens()
    if not tokens.get("algoliaSearchAPIKey"):
        logger.error("Could not obtain Algolia tokens. Scrape aborted.")
        return []

    new_listings = []
    session = get_session()

    try:
        # Step 2: Search for auctions near Vancouver
        logger.info("Searching for active auctions near Vancouver...")
        all_auction_ids = []

        page = 0
        while True:
            results = search_auctions(tokens, page=page)
            hits = results.get("hits", [])
            if not hits:
                break
            for hit in hits:
                auction_id = hit.get("am_auction_id") or hit.get("objectID")
                if auction_id:
                    all_auction_ids.append(auction_id)
            # Check if there are more pages
            if page >= results.get("nbPages", 1) - 1:
                break
            page += 1

        logger.info(f"Found {len(all_auction_ids)} active auctions near Vancouver")

        # Step 3: For each auction, get items and check for camera keywords
        for auction_id in all_auction_ids:
            try:
                # Polite delay between auction requests
                time.sleep(random.uniform(2, 3))

                items_data = get_auction_items(auction_id)
                items = items_data.get("items", [])

                for item in items:
                    item_id = item.get("id")
                    if not item_id:
                        continue

                    title = item.get("title", "")
                    description = item.get("item_description", item.get("description", ""))
                    combined_text = f"{title} {description}"

                    # Check keyword match
                    category = _match_keywords(combined_text, keywords_config, exclude_keywords)
                    if not category:
                        continue

                    item_url = _build_item_url(auction_id, item_id)

                    # Check if already in DB
                    existing = session.query(Listing).filter_by(maxsold_url=item_url).first()

                    if existing:
                        # Record bid history for existing listing
                        current_bid = float(item.get("current_bid", 0))
                        if current_bid > 0:
                            bid_record = BidHistory(
                                listing_id=existing.id,
                                bid_amount=current_bid,
                            )
                            session.add(bid_record)
                        continue

                    # New listing — get detailed data
                    try:
                        time.sleep(random.uniform(1, 2))
                        detail = get_item_detail(item_id)
                    except Exception as e:
                        logger.warning(f"Failed to get detail for item {item_id}: {e}")
                        detail = item  # Use basic item data as fallback

                    photo_urls = _parse_photo_urls(detail)
                    current_bid = float(detail.get("current_bid", item.get("current_bid", 0)))
                    end_time = _parse_auction_end_time(detail)

                    listing = Listing(
                        title=title,
                        description=description,
                        photo_urls=photo_urls,
                        current_bid=current_bid,
                        auction_end_time=end_time,
                        maxsold_url=item_url,
                        category=category,
                    )
                    session.add(listing)
                    session.flush()  # Get the ID assigned

                    # Also record initial bid history
                    if current_bid > 0:
                        bid_record = BidHistory(
                            listing_id=listing.id,
                            bid_amount=current_bid,
                        )
                        session.add(bid_record)

                    new_listings.append(listing)
                    logger.info(f"New listing: [{category}] {title} — ${current_bid:.2f}")

            except Exception as e:
                logger.warning(f"Error processing auction {auction_id}: {e}")
                continue

        session.commit()
        logger.info(f"Scrape complete: {len(new_listings)} new camera listings found")

    except Exception as e:
        logger.error(f"Scrape failed: {e}")
        session.rollback()
    finally:
        session.close()

    return new_listings


def run_scraper(config: dict) -> list:
    """Synchronous wrapper for the scraping pipeline."""
    return scrape_maxsold(config)
