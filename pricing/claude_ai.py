"""
Rule-based pricing engine for camera listings.
Compares eBay sold comps against the current bid to estimate value,
score condition from description keywords, and flag deals.

No API keys required — pure math and keyword analysis.
"""

import logging
import re

from db.database import get_session
from db.models import PriceResearch

logger = logging.getLogger(__name__)

# Keywords that indicate condition level (checked against description, case-insensitive)
CONDITION_KEYWORDS = {
    "Excellent": [
        "mint", "like new", "excellent", "pristine", "perfect",
        "barely used", "immaculate", "flawless",
    ],
    "Good": [
        "good condition", "works well", "working", "functions",
        "tested", "clean", "nice", "well maintained", "light wear",
    ],
    "Fair": [
        "fair", "some wear", "scratches", "scuffs", "signs of use",
        "cosmetic", "used", "wear", "aged", "patina",
    ],
    "Parts Only": [
        "parts only", "parts or repair", "not working", "broken",
        "as-is", "as is", "for parts", "untested", "repair",
        "damaged", "crack", "fungus", "haze", "stuck", "jammed",
    ],
}

# FB Marketplace typically sells at ~80-90% of eBay prices (local, no shipping)
FB_MARKETPLACE_FACTOR = 0.85


def _score_condition(description: str) -> tuple[str, str]:
    """
    Analyze listing description text to estimate condition.
    Returns (condition_score, condition_notes).
    """
    desc_lower = description.lower()

    # Check from best to worst — more specific phrases like "barely used" match before generic "used"
    for condition in ["Excellent", "Good", "Parts Only", "Fair"]:
        matched_keywords = []
        for kw in CONDITION_KEYWORDS[condition]:
            # Use word boundary matching to avoid "untested" matching "tested"
            pattern = r'(?<!\w)' + re.escape(kw) + r'(?!\w)'
            if re.search(pattern, desc_lower):
                matched_keywords.append(kw)
        if matched_keywords:
            notes = f"Matched: {', '.join(matched_keywords[:3])}"
            return condition, notes

    # No matches — default to Fair (conservative)
    return "Fair", "No condition indicators found in description"


def research_listing(listing: dict, ebay_comps: dict, config: dict = None) -> dict:
    """
    Rule-based pricing analysis for a camera listing.

    Uses eBay sold comps and description keyword analysis to produce
    the same output structure as the old AI-based approach.

    Args:
        listing: dict with title, description, current_bid, auction_end_time
        ebay_comps: dict from get_ebay_sold_comps()
        config: full config dict (optional)

    Returns:
        dict with estimated_value, max_bid_price, fb_marketplace_ceiling,
        condition_score, condition_notes, deal_flag, summary.
    """
    if config is None:
        config = {}

    margin_target = config.get("business", {}).get("margin_target", 0.50)
    current_bid = listing.get("current_bid", 0)
    title = listing.get("title", "Unknown camera")
    description = listing.get("description", "")

    # Estimate value from eBay comps
    avg_sold = ebay_comps.get("average_sold", 0)
    min_sold = ebay_comps.get("min_sold", 0)
    max_sold = ebay_comps.get("max_sold", 0)
    sample_count = ebay_comps.get("sample_count", 0)

    # Score condition from description
    condition_score, condition_notes = _score_condition(description)

    # Adjust estimated value based on condition
    condition_multiplier = {
        "Excellent": 1.1,  # Can sell above average
        "Good": 1.0,       # Average price
        "Fair": 0.8,       # Below average
        "Parts Only": 0.4, # Significant discount
    }.get(condition_score, 0.8)

    if avg_sold > 0:
        estimated_value = round(avg_sold * condition_multiplier, 2)
    else:
        # No comps — can't estimate
        estimated_value = 0

    max_bid_price = round(estimated_value * margin_target, 2)
    fb_marketplace_ceiling = round(estimated_value * FB_MARKETPLACE_FACTOR, 2)
    deal_flag = current_bid < max_bid_price if max_bid_price > 0 else False

    # Build summary
    if estimated_value > 0 and deal_flag:
        savings = max_bid_price - current_bid
        summary = (
            f"{title} — estimated resale ${estimated_value:.0f} CAD, "
            f"current bid ${current_bid:.0f} is ${savings:.0f} under your max bid target."
        )
    elif estimated_value > 0:
        summary = (
            f"{title} — estimated resale ${estimated_value:.0f} CAD "
            f"({sample_count} eBay comps), condition: {condition_score}. "
            f"Current bid ${current_bid:.0f} exceeds the ${max_bid_price:.0f} target."
        )
    else:
        summary = (
            f"{title} — no eBay sold data found. "
            f"Manual research recommended before bidding."
        )

    result = {
        "estimated_value": estimated_value,
        "max_bid_price": max_bid_price,
        "fb_marketplace_ceiling": fb_marketplace_ceiling,
        "condition_score": condition_score,
        "condition_notes": condition_notes,
        "deal_flag": deal_flag,
        "summary": summary,
    }

    logger.info(
        f"Priced '{title}': est=${estimated_value:.2f}, "
        f"condition={condition_score}, deal={deal_flag}"
    )
    return result


def save_research(listing_id: int, research: dict, ebay_comps: dict) -> PriceResearch:
    """Save pricing research results to the database."""
    session = get_session()
    try:
        price_research = PriceResearch(
            listing_id=listing_id,
            estimated_value=research.get("estimated_value", 0),
            max_bid_price=research.get("max_bid_price", 0),
            fb_marketplace_ceiling=research.get("fb_marketplace_ceiling", 0),
            condition_score=research.get("condition_score", ""),
            condition_notes=research.get("condition_notes", ""),
            deal_flag=research.get("deal_flag", False),
            ebay_comps_raw=ebay_comps,
        )
        session.add(price_research)
        session.commit()
        logger.info(f"Saved research for listing {listing_id}")
        return price_research
    except Exception as e:
        logger.error(f"Failed to save research for listing {listing_id}: {e}")
        session.rollback()
        raise
    finally:
        session.close()
