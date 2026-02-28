import base64
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# USD to CAD conversion rate (approximate)
USD_TO_CAD = 1.35

# Module-level token cache
_token_cache = {
    "access_token": None,
    "expires_at": 0,
}

EBAY_AUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"


def _get_token(config: dict) -> str:
    """Get eBay OAuth2 access token using client credentials flow. Caches and auto-refreshes."""
    global _token_cache

    # Return cached token if still valid (with 60s buffer)
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    app_id = config["ebay"]["app_id"]
    app_secret = config["ebay"].get("app_secret", "")

    # Base64 encode client credentials
    credentials = base64.b64encode(f"{app_id}:{app_secret}".encode()).decode()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {credentials}",
    }

    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope",
    }

    resp = requests.post(EBAY_AUTH_URL, headers=headers, data=data, timeout=15)
    resp.raise_for_status()

    token_data = resp.json()
    _token_cache["access_token"] = token_data["access_token"]
    _token_cache["expires_at"] = time.time() + token_data.get("expires_in", 7200)

    logger.info("eBay OAuth2 token obtained/refreshed")
    return _token_cache["access_token"]


def _search_sold_items(query: str, token: str, days_back: int = 90) -> list:
    """Search eBay for sold/completed items matching the query."""
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        "Content-Type": "application/json",
    }

    params = {
        "q": query,
        "filter": "conditionIds:{1000|1500|2000|2500|3000},buyingOptions:{FIXED_PRICE|AUCTION}",
        "sort": "-price",
        "limit": "50",
    }

    resp = requests.get(EBAY_BROWSE_URL, headers=headers, params=params, timeout=15)

    if resp.status_code == 404:
        return []
    resp.raise_for_status()

    data = resp.json()
    return data.get("itemSummaries", [])


def _extract_price(item: dict) -> Optional[float]:
    """Extract the price from an eBay item summary."""
    price_info = item.get("price", {})
    value = price_info.get("value")
    if value:
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    return None


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
    if any(term in model_lower for term in ["canon", "nikon", "pentax", "minolta", "olympus"]):
        if any(term in model_lower for term in ["ae-1", "a-1", "f-1", "fm", "fe", "k1000", "x-700", "om-1"]):
            queries.append(f"{words[0]} film camera" if words else "film camera")
    if any(term in model_lower for term in ["bolex", "super 8", "8mm"]):
        queries.append("vintage movie camera")
    if any(term in model_lower for term in ["hasselblad", "rolleiflex", "mamiya"]):
        queries.append("medium format camera")

    return queries


def get_ebay_sold_comps(camera_model: str, config: dict) -> dict:
    """
    Get sold comparable listings from eBay for a camera model.

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

    try:
        token = _get_token(config)
    except Exception as e:
        logger.error(f"eBay auth failed: {e}")
        return result

    queries = _generate_fallback_queries(camera_model)
    items = []

    for query in queries:
        try:
            items = _search_sold_items(query, token, config["ebay"].get("sold_days_lookback", 90))
            if items:
                logger.info(f"eBay: Found {len(items)} comps for query '{query}'")
                break
            logger.info(f"eBay: No results for '{query}', trying broader search...")
        except Exception as e:
            logger.warning(f"eBay search failed for '{query}': {e}")
            continue

    if not items:
        logger.warning(f"eBay: No comparable sales found for '{camera_model}'")
        return result

    # Extract prices and convert to CAD
    prices_cad = []
    raw_listings = []

    for item in items:
        price_usd = _extract_price(item)
        if price_usd is None or price_usd <= 0:
            continue

        price_cad = price_usd * USD_TO_CAD
        prices_cad.append(price_cad)

        raw_listings.append({
            "title": item.get("title", ""),
            "price_usd": price_usd,
            "price_cad": round(price_cad, 2),
            "condition": item.get("condition", ""),
            "item_web_url": item.get("itemWebUrl", ""),
        })

    if not prices_cad:
        return result

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
