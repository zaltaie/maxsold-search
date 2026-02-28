import logging
import re
import time
import random
from typing import Optional
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

# USD to CAD conversion rate (approximate)
USD_TO_CAD = 1.35

EBAY_SEARCH_URL = "https://www.ebay.com/sch/i.html"

# Common user agents to rotate through
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def _get_headers() -> dict:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _parse_price(price_text: str) -> Optional[float]:
    """Extract a numeric price from eBay price text like '$145.00' or 'C $200.00'."""
    match = re.search(r"[\$]?\s*([\d,]+\.?\d*)", price_text.replace(",", ""))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _scrape_ebay_sold(query: str) -> list:
    """
    Scrape eBay completed/sold listings for a search query.
    Returns a list of dicts with title, price_usd, and url.
    No API key needed — uses public search pages.
    """
    params = {
        "_nkw": query,
        "LH_Complete": "1",  # Completed listings
        "LH_Sold": "1",      # Sold only
        "_sop": "13",        # Sort by end date: recent first
        "_ipg": "60",        # Results per page
    }

    try:
        resp = requests.get(EBAY_SEARCH_URL, params=params, headers=_get_headers(), timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"eBay scrape failed for '{query}': {e}")
        return []

    html = resp.text
    items = []

    # Parse sold items from search results HTML
    # eBay uses s-item class for each result
    item_blocks = re.split(r'class="s-item__wrapper', html)

    for block in item_blocks[1:]:  # Skip first split (before any item)
        # Extract title
        title_match = re.search(
            r'class="s-item__title"[^>]*>(?:<span[^>]*>)?(.*?)(?:</span>)?</(?:div|h3|span)',
            block, re.DOTALL
        )
        if not title_match:
            continue
        title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()
        if not title or title.lower() == "shop on ebay":
            continue

        # Extract sold price
        price_match = re.search(
            r'class="s-item__price"[^>]*>(.*?)</span',
            block, re.DOTALL
        )
        if not price_match:
            continue
        price_text = re.sub(r"<[^>]+>", "", price_match.group(1)).strip()

        # Skip price ranges like "$50.00 to $100.00"
        if " to " in price_text.lower():
            continue

        price_usd = _parse_price(price_text)
        if price_usd is None or price_usd <= 0:
            continue

        # Extract URL
        url_match = re.search(r'href="(https://www\.ebay\.com/itm/[^"]+)"', block)
        url = url_match.group(1) if url_match else ""

        items.append({
            "title": title,
            "price_usd": price_usd,
            "url": url,
        })

    return items


def _generate_fallback_queries(camera_model: str) -> list:
    """Generate progressively broader search queries for fallback."""
    queries = [camera_model]
    words = camera_model.split()

    # Remove words from the end to broaden the search
    while len(words) > 1:
        words = words[:-1]
        queries.append(" ".join(words))

    # Add generic category fallbacks
    model_lower = camera_model.lower()
    first_word = camera_model.split()[0] if camera_model.split() else ""
    if any(term in model_lower for term in ["canon", "nikon", "pentax", "minolta", "olympus"]):
        if any(term in model_lower for term in ["ae-1", "a-1", "f-1", "fm", "fe", "k1000", "x-700", "om-1"]):
            queries.append(f"{first_word} film camera")
    if any(term in model_lower for term in ["bolex", "super 8", "8mm"]):
        queries.append("vintage movie camera")
    if any(term in model_lower for term in ["hasselblad", "rolleiflex", "mamiya"]):
        queries.append("medium format camera")

    return queries


def get_ebay_sold_comps(camera_model: str, config: dict = None) -> dict:
    """
    Get sold comparable listings from eBay by scraping public search pages.
    No API key or login required.

    Returns a dict with average, min, max sold prices (in CAD),
    sample count, and raw listing data.
    """
    result = {
        "average_sold": 0.0,
        "min_sold": 0.0,
        "max_sold": 0.0,
        "sample_count": 0,
        "currency": "CAD",
        "raw_listings": [],
    }

    queries = _generate_fallback_queries(camera_model)
    items = []

    for query in queries:
        # Polite delay between requests
        time.sleep(random.uniform(1.5, 3.0))

        items = _scrape_ebay_sold(query)
        if items:
            logger.info(f"eBay: Found {len(items)} sold comps for '{query}'")
            break
        logger.info(f"eBay: No results for '{query}', trying broader search...")

    if not items:
        logger.warning(f"eBay: No comparable sales found for '{camera_model}'")
        return result

    # Convert to CAD and build result
    prices_cad = []
    raw_listings = []

    for item in items:
        price_cad = item["price_usd"] * USD_TO_CAD
        prices_cad.append(price_cad)
        raw_listings.append({
            "title": item["title"],
            "price_usd": item["price_usd"],
            "price_cad": round(price_cad, 2),
            "url": item.get("url", ""),
        })

    result["average_sold"] = round(sum(prices_cad) / len(prices_cad), 2)
    result["min_sold"] = round(min(prices_cad), 2)
    result["max_sold"] = round(max(prices_cad), 2)
    result["sample_count"] = len(prices_cad)
    result["raw_listings"] = raw_listings

    logger.info(
        f"eBay comps for '{camera_model}': "
        f"avg=${result['average_sold']:.2f} CAD, "
        f"range=${result['min_sold']:.2f}-${result['max_sold']:.2f}, "
        f"n={result['sample_count']}"
    )

    return result
