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
# Ordered from most specific to least specific within each category
CONDITION_KEYWORDS = {
    "Excellent": [
        "mint condition", "mint", "like new", "excellent condition", "excellent",
        "pristine", "perfect condition", "perfect", "barely used", "immaculate",
        "flawless", "near mint", "new in box", "never used", "unused",
    ],
    "Good": [
        "good condition", "works well", "fully working", "working condition",
        "working", "fully functional", "functions properly", "functions",
        "tested working", "tested and working", "tested", "clean",
        "well maintained", "light wear", "minor wear", "nice condition", "nice",
        "good shape", "great condition", "very good",
    ],
    "Fair": [
        "fair condition", "fair", "some wear", "scratches", "scuffs",
        "signs of use", "cosmetic damage", "cosmetic wear", "cosmetic",
        "heavy wear", "moderate wear", "well used", "used condition",
        "aged", "patina", "brassing", "paint wear", "faded",
    ],
    "Parts Only": [
        "parts only", "parts or repair", "for parts or repair",
        "not working", "not functioning", "does not work", "broken",
        "as-is", "as is", "for parts", "untested", "needs repair", "repair",
        "damaged", "cracked", "crack", "shutter stuck", "stuck shutter",
        "fungus", "heavy fungus", "lens fungus", "haze", "lens haze",
        "separation", "balsam separation", "stuck", "jammed", "seized",
        "corroded", "corrosion", "battery leak", "mold", "mould",
        "missing parts", "incomplete", "water damage", "fog", "foggy lens",
    ],
}

# FB Marketplace typically sells at ~80-90% of eBay prices (local, no shipping)
FB_MARKETPLACE_FACTOR = 0.85


def _score_condition(description: str) -> tuple[str, str]:
    """
    Analyze listing description text to estimate condition.
    Uses a scoring system — all categories are checked and the one with
    the strongest signal wins. This prevents generic words like "used"
    from overriding more specific matches like "barely used" or "fungus".

    Returns (condition_score, condition_notes).
    """
    desc_lower = description.lower()

    # Score each condition level by counting keyword matches with weights
    # Longer/more specific phrases get higher weight
    scores = {}
    all_matches = {}

    for condition in CONDITION_KEYWORDS:
        score = 0
        matches = []
        for kw in CONDITION_KEYWORDS[condition]:
            pattern = r'(?<!\w)' + re.escape(kw) + r'(?!\w)'
            if re.search(pattern, desc_lower):
                # Weight by phrase length — multi-word phrases are more specific
                weight = len(kw.split())
                score += weight
                matches.append(kw)
        scores[condition] = score
        all_matches[condition] = matches

    # "Parts Only" keywords are strong negative signals — boost their weight
    # because a camera with fungus/haze is definitively parts-only
    if scores.get("Parts Only", 0) > 0:
        scores["Parts Only"] *= 2

    # Pick the condition with the highest score
    best = max(scores, key=scores.get)
    if scores[best] > 0:
        notes = f"Matched: {', '.join(all_matches[best][:3])}"
        return best, notes

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

    # Estimate value from eBay comps — prefer median over average for robustness
    avg_sold = ebay_comps.get("median_sold", 0) or ebay_comps.get("average_sold", 0)
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

    # Calculate profit metrics
    potential_profit = round(estimated_value - current_bid, 2) if estimated_value > 0 else 0
    roi_percent = round((potential_profit / current_bid) * 100, 1) if current_bid > 0 and estimated_value > 0 else 0
    profit_after_fees = round(potential_profit * 0.87, 2)  # ~13% for platform fees + shipping

    # Build summary
    if estimated_value > 0 and deal_flag:
        savings = max_bid_price - current_bid
        summary = (
            f"{title} — estimated resale ${estimated_value:.0f} CAD, "
            f"current bid ${current_bid:.0f} is ${savings:.0f} under your max bid target. "
            f"Potential profit: ${potential_profit:.0f} ({roi_percent:.0f}% ROI)."
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
        "potential_profit": potential_profit,
        "roi_percent": roi_percent,
        "profit_after_fees": profit_after_fees,
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
