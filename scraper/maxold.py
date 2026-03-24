import asyncio
import json
import logging
import random
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

import requests
from playwright.async_api import async_playwright

from db.database import get_session
from db.models import Listing, BidHistory

logger = logging.getLogger(__name__)

# Region coordinates for Algolia geosearch
REGION_COORDS = {
    "vancouver": "49.2827,-123.1207",
    "toronto": "43.6532,-79.3832",
    "calgary": "51.0447,-114.0719",
    "montreal": "45.5017,-73.5673",
    "ottawa": "45.4215,-75.6972",
    "edmonton": "53.5461,-113.4938",
    "victoria": "48.4284,-123.3656",
    "winnipeg": "49.8951,-97.1384",
}
DEFAULT_REGION = "vancouver"
SEARCH_RADIUS_METERS = 100000  # 100 km

# Algolia endpoint template
ALGOLIA_BASE = "https://bwhj2cu1lu-dsn.algolia.net/1/indexes"
ALGOLIA_AGENT = "Algolia%20for%20JavaScript%20(4.11.0)%3B%20Browser"

# Maxsold internal API endpoints
MAXSOLD_API_ITEMS = "https://maxsold.maxsold.com/api/getitems"
MAXSOLD_API_ITEM_DATA = "https://maxsold.maxsold.com/api/itemdata"

# Token cache — avoids re-extracting Algolia credentials every scrape
_token_cache = {"tokens": None, "fetched_at": 0, "lock": threading.Lock()}
TOKEN_CACHE_TTL = 3600  # 1 hour


def _extract_algolia_tokens(js_text: str) -> dict:
    """Extract Algolia API key and app ID from Maxsold's JS bundle.
    Handles multiple bundler formats: key:"value", "key":"value", key: "value"."""
    tokens = {}

    # Try multiple patterns for different bundler outputs
    patterns = [
        # Pattern 1: key:"value" (webpack/next.js minified)
        (r'algoliaApplicationId["\s]*:\s*"([^"]+)"', "algoliaApplicationId"),
        (r'algoliaSearchAPIKey["\s]*:\s*"([^"]+)"', "algoliaSearchAPIKey"),
        # Pattern 2: NEXT_PUBLIC_ALGOLIA env vars (Next.js)
        (r'NEXT_PUBLIC_ALGOLIA_APP_ID["\s]*:\s*"([^"]+)"', "algoliaApplicationId"),
        (r'NEXT_PUBLIC_ALGOLIA_SEARCH_KEY["\s]*:\s*"([^"]+)"', "algoliaSearchAPIKey"),
        # Pattern 3: Generic Algolia patterns
        (r'(?:applicationId|appId|app_id)["\s]*:\s*"([A-Z0-9]{10,})"', "algoliaApplicationId"),
        (r'(?:searchApiKey|search_api_key|apiKey)["\s]*:\s*"([a-f0-9]{20,})"', "algoliaSearchAPIKey"),
    ]

    for pattern, key in patterns:
        if key not in tokens:
            match = re.search(pattern, js_text, re.IGNORECASE)
            if match:
                tokens[key] = match.group(1)

    return tokens


def _is_maxsold_js_url(url: str) -> bool:
    """Check if a JS URL belongs to Maxsold (not Google Maps, analytics, etc.)."""
    # Must be from maxsold.com domain or a relative Next.js path
    if "maxsold.com" in url or "/_next/" in url:
        return True
    # Reject known third-party domains
    third_party = ["googleapis.com", "google.com", "gstatic.com", "facebook",
                   "analytics", "gtag", "hotjar", "sentry", "segment"]
    return not any(tp in url for tp in third_party)


async def _get_algolia_tokens_via_playwright() -> dict:
    """Use Playwright to load Maxsold and extract Algolia credentials.

    Strategy (in order of reliability):
    1. Intercept actual Algolia API network requests to capture credentials in-flight
    2. Scan Next.js chunk files for Algolia config variables
    3. Fall back to scanning page source inline scripts
    """
    tokens = {}
    captured_tokens = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        js_urls = []

        # Strategy 1: Intercept Algolia API requests directly
        # When Maxsold initializes Algolia, it sends requests with headers containing the tokens
        async def handle_request(request):
            url = request.url
            headers = request.headers
            if "algolia" in url.lower() or "algolianet.com" in url.lower():
                api_key = headers.get("x-algolia-api-key", "")
                app_id = headers.get("x-algolia-application-id", "")
                if api_key and app_id:
                    captured_tokens["algoliaSearchAPIKey"] = api_key
                    captured_tokens["algoliaApplicationId"] = app_id
                    logger.info("Captured Algolia tokens from network request")

        # Strategy 2: Collect Maxsold JS bundle URLs for scanning
        async def handle_response(response):
            url = response.url
            if url.endswith(".js") and _is_maxsold_js_url(url):
                js_urls.append(url)

        page.on("request", handle_request)
        page.on("response", handle_response)

        try:
            await page.goto("https://maxsold.com", wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)
        except Exception as e:
            logger.warning(f"Page load timeout (non-fatal): {e}")

        # Check if we captured tokens from network requests (best method)
        if captured_tokens.get("algoliaSearchAPIKey"):
            await browser.close()
            return captured_tokens

        # Strategy 2: Scan captured JS bundle files
        # Prioritize _next/static/chunks files (where Next.js puts env config)
        chunk_urls = sorted(js_urls, key=lambda u: (
            0 if "chunks" in u else 1,  # chunks files first
            0 if "app" in u or "page" in u else 1,  # app/page chunks first
            0 if "main" in u else 1,  # main bundles next
        ))

        for js_url in chunk_urls:
            try:
                js_resp = await page.evaluate(f"""
                    async () => {{
                        const resp = await fetch("{js_url}");
                        return await resp.text();
                    }}
                """)
                tokens = _extract_algolia_tokens(js_resp)
                if tokens.get("algoliaSearchAPIKey"):
                    logger.info(f"Found Algolia tokens in JS bundle: {js_url.split('/')[-1]}")
                    break
            except Exception as e:
                logger.debug(f"Failed to fetch JS bundle {js_url}: {e}")
                continue

        # Strategy 3: Check inline scripts on the page
        if not tokens.get("algoliaSearchAPIKey"):
            try:
                # Next.js often injects __NEXT_DATA__ with env variables
                next_data = await page.evaluate("""
                    () => {
                        const el = document.getElementById('__NEXT_DATA__');
                        return el ? el.textContent : '';
                    }
                """)
                if next_data:
                    tokens = _extract_algolia_tokens(next_data)
                    if tokens.get("algoliaSearchAPIKey"):
                        logger.info("Found Algolia tokens in __NEXT_DATA__")
            except Exception:
                pass

        # Strategy 4: Check all script tags content
        if not tokens.get("algoliaSearchAPIKey"):
            try:
                all_scripts = await page.evaluate("""
                    () => {
                        return Array.from(document.querySelectorAll('script:not([src])')).map(s => s.textContent).join('\\n');
                    }
                """)
                if all_scripts:
                    tokens = _extract_algolia_tokens(all_scripts)
                    if tokens.get("algoliaSearchAPIKey"):
                        logger.info("Found Algolia tokens in inline scripts")
            except Exception:
                pass

        await browser.close()

    return tokens or captured_tokens


def _get_algolia_tokens_direct() -> dict:
    """Try to extract Algolia tokens by fetching the Maxsold JS bundle directly.

    Scans the HTML for all Next.js chunk file references and checks each one
    for Algolia config. Also checks __NEXT_DATA__ in the HTML itself.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        resp = requests.get("https://maxsold.com", timeout=15, headers=headers)
        html = resp.text

        # Check __NEXT_DATA__ in the HTML first (fastest)
        next_data_match = re.search(r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if next_data_match:
            tokens = _extract_algolia_tokens(next_data_match.group(1))
            if tokens.get("algoliaSearchAPIKey"):
                logger.info("Found Algolia tokens in __NEXT_DATA__ (direct)")
                return tokens

        # Also check all inline scripts
        inline_scripts = re.findall(r'<script[^>]*>([^<]+algolia[^<]+)</script>', html, re.IGNORECASE)
        for script_content in inline_scripts:
            tokens = _extract_algolia_tokens(script_content)
            if tokens.get("algoliaSearchAPIKey"):
                logger.info("Found Algolia tokens in inline script (direct)")
                return tokens

        # Find all Next.js chunk files (not just main.*.js)
        # Next.js uses paths like /_next/static/chunks/*.js
        js_matches = re.findall(r'(?:src|href)=["\']([^"\']*/_next/static/[^"\']*\.js)["\']', html)

        # Also match plain main.*.js pattern
        js_matches += re.findall(r'(?:src|href)=["\']([^"\']*(?:main|app|page|webpack)\.[a-f0-9]+\.js)["\']', html)

        # Deduplicate and prioritize chunks files
        seen = set()
        js_urls = []
        for m in js_matches:
            if m not in seen:
                seen.add(m)
                js_urls.append(m)

        # Prioritize: chunks > app > main
        js_urls.sort(key=lambda u: (
            0 if "chunks" in u else 1,
            0 if "app" in u or "page" in u else 1,
        ))

        for js_path in js_urls[:15]:  # Limit to 15 files to avoid excessive requests
            url = js_path
            if not url.startswith("http"):
                url = f"https://maxsold.com{url}" if url.startswith("/") else f"https://maxsold.com/{url}"

            try:
                time.sleep(random.uniform(0.2, 0.5))
                js_resp = requests.get(url, timeout=10, headers=headers)
                tokens = _extract_algolia_tokens(js_resp.text)
                if tokens.get("algoliaSearchAPIKey"):
                    logger.info(f"Found Algolia tokens in {url.split('/')[-1]} (direct)")
                    return tokens
            except Exception as e:
                logger.debug(f"Failed to fetch {url}: {e}")
                continue

        return {}
    except Exception as e:
        logger.warning(f"Direct token extraction failed: {e}")
        return {}


def _run_playwright_in_thread() -> dict:
    """Run Playwright token extraction in a dedicated thread with its own event loop.
    This avoids asyncio.run() deadlocks when called from within an existing event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_get_algolia_tokens_via_playwright())
    finally:
        loop.close()


def get_algolia_tokens() -> dict:
    """Get Algolia tokens with caching. Tries direct HTTP first, then Playwright fallback.
    Cached for 1 hour to avoid redundant extraction."""
    now = time.time()
    with _token_cache["lock"]:
        cached = _token_cache["tokens"]
        if cached and (now - _token_cache["fetched_at"]) < TOKEN_CACHE_TTL:
            if cached.get("algoliaSearchAPIKey") and cached.get("algoliaApplicationId"):
                logger.debug("Using cached Algolia tokens")
                return cached

    # Try direct HTTP first (fast, no browser needed)
    tokens = _get_algolia_tokens_direct()
    if tokens.get("algoliaSearchAPIKey") and tokens.get("algoliaApplicationId"):
        logger.info("Algolia tokens extracted via direct HTTP")
        with _token_cache["lock"]:
            _token_cache["tokens"] = tokens
            _token_cache["fetched_at"] = now
        return tokens

    # Playwright fallback — run in a separate thread to avoid event loop conflicts
    logger.info("Falling back to Playwright for token extraction")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_playwright_in_thread)
            tokens = future.result(timeout=60)
    except Exception as e:
        logger.error(f"Playwright token extraction failed: {e}")
        tokens = {}

    if tokens.get("algoliaSearchAPIKey"):
        logger.info("Algolia tokens extracted via Playwright")
        with _token_cache["lock"]:
            _token_cache["tokens"] = tokens
            _token_cache["fetched_at"] = now
    else:
        logger.error("Failed to extract Algolia tokens via all methods")
    return tokens


def _get_region_coords(config: dict) -> str:
    """Get lat/lng string for the configured region."""
    region = config.get("maxsold", {}).get("region", DEFAULT_REGION).lower()
    return REGION_COORDS.get(region, REGION_COORDS[DEFAULT_REGION])


def search_auctions(tokens: dict, page: int = 0, region_coords: str = None) -> dict:
    """Search for active auctions near the configured region using the Algolia API."""
    if region_coords is None:
        region_coords = REGION_COORDS[DEFAULT_REGION]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-algolia-api-key": tokens["algoliaSearchAPIKey"],
        "x-algolia-application-id": tokens["algoliaApplicationId"],
        "Content-Type": "application/json",
    }

    now = int(time.time())
    end_threshold = now - 900

    data = json.dumps({
        "query": "",
        "filters": f"start_date <= {now} AND end_date > {end_threshold}",
        "facetFilters": ["auction_phase:-cancelledAuction"],
        "hitsPerPage": 100,
        "page": page,
        "aroundLatLng": region_coords,
        "aroundLatLngViaIP": False,
        "aroundRadius": SEARCH_RADIUS_METERS,
    })

    url = f"{ALGOLIA_BASE}/hpauction/query?x-algolia-agent={ALGOLIA_AGENT}"
    resp = requests.post(url, headers=headers, data=data, timeout=15)
    resp.raise_for_status()
    return resp.json()


def search_items(tokens: dict, query: str = "", page: int = 0, region_coords: str = None) -> dict:
    """Search for individual items across auctions using the Algolia API."""
    if region_coords is None:
        region_coords = REGION_COORDS[DEFAULT_REGION]

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
        "aroundLatLng": region_coords,
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
    region_coords = _get_region_coords(config)
    region_name = config.get("maxsold", {}).get("region", DEFAULT_REGION).title()

    try:
        # Step 2: Search for auctions near the configured region
        logger.info(f"Searching for active auctions near {region_name}...")
        all_auction_ids = []

        page = 0
        while True:
            results = search_auctions(tokens, page=page, region_coords=region_coords)
            hits = results.get("hits", [])
            if not hits:
                break
            for hit in hits:
                auction_id = hit.get("am_auction_id") or hit.get("objectID")
                if auction_id:
                    all_auction_ids.append(auction_id)
            if page >= results.get("nbPages", 1) - 1:
                break
            page += 1

        logger.info(f"Found {len(all_auction_ids)} active auctions near {region_name}")

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
