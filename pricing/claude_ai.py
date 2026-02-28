import json
import logging

import anthropic

from db.database import get_session
from db.models import PriceResearch

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a camera pricing expert helping a Vancouver reseller evaluate auction listings.
You will be given a Maxsold listing and eBay sold comps data.
Respond ONLY with a valid JSON object. No preamble, no explanation."""


def _build_user_prompt(listing: dict, ebay_comps: dict, config: dict) -> str:
    """Construct the user prompt with listing and comp data."""
    margin_target = config.get("business", {}).get("margin_target", 0.50)
    margin_pct = int(margin_target * 100)

    return f"""Listing title: {listing.get('title', 'Unknown')}
Listing description: {listing.get('description', 'No description')}
Current bid: ${listing.get('current_bid', 0):.2f} CAD
Auction ends: {listing.get('auction_end_time', 'Unknown')}

eBay sold comps (last 90 days):
- Average sold price: ${ebay_comps.get('average_sold', 0):.2f} CAD
- Range: ${ebay_comps.get('min_sold', 0):.2f} to ${ebay_comps.get('max_sold', 0):.2f} CAD
- Sample size: {ebay_comps.get('sample_count', 0)} listings

Margin target: {margin_pct}% (buy at {margin_pct}% of estimated resale value)

Return this exact JSON structure:
{{
  "estimated_value": <number in CAD>,
  "max_bid_price": <number in CAD, {margin_pct}% of estimated_value>,
  "fb_marketplace_ceiling": <number in CAD, estimated max FB Marketplace Vancouver sell price>,
  "condition_score": "<Excellent | Good | Fair | Parts Only>",
  "condition_notes": "<brief notes on condition indicators found in listing text>",
  "deal_flag": <true if current bid is below max_bid_price, false otherwise>,
  "summary": "<one sentence plain English summary for the seller>"
}}"""


def research_listing(listing: dict, ebay_comps: dict, config: dict) -> dict:
    """
    Use Claude API to analyze a listing with eBay comp data.

    Args:
        listing: dict with title, description, current_bid, auction_end_time
        ebay_comps: dict from get_ebay_sold_comps()
        config: full config dict

    Returns:
        dict with pricing analysis fields, or empty dict on failure.
    """
    api_key = config.get("claude", {}).get("api_key", "")
    model = config.get("claude", {}).get("model", "claude-sonnet-4-20250514")

    if not api_key or api_key == "YOUR_ANTHROPIC_API_KEY":
        logger.warning("Claude API key not configured, skipping AI research")
        return _fallback_research(listing, ebay_comps, config)

    client = anthropic.Anthropic(api_key=api_key)
    user_prompt = _build_user_prompt(listing, ebay_comps, config)

    try:
        message = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Extract text content from response
        response_text = ""
        for block in message.content:
            if hasattr(block, "text"):
                response_text += block.text

        # Parse JSON response
        result = json.loads(response_text.strip())

        # Validate expected fields
        expected_fields = [
            "estimated_value", "max_bid_price", "fb_marketplace_ceiling",
            "condition_score", "condition_notes", "deal_flag", "summary"
        ]
        for field in expected_fields:
            if field not in result:
                logger.warning(f"Missing field '{field}' in Claude response")

        logger.info(
            f"AI research for '{listing.get('title', '?')}': "
            f"est=${result.get('estimated_value', 0):.2f}, "
            f"deal={result.get('deal_flag', False)}"
        )
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude JSON response: {e}")
        logger.debug(f"Raw response: {response_text[:500]}")
        return _fallback_research(listing, ebay_comps, config)

    except anthropic.APIError as e:
        logger.error(f"Claude API error: {e}")
        return _fallback_research(listing, ebay_comps, config)

    except Exception as e:
        logger.error(f"Unexpected error in AI research: {e}")
        return _fallback_research(listing, ebay_comps, config)


def _fallback_research(listing: dict, ebay_comps: dict, config: dict) -> dict:
    """Simple rule-based fallback when Claude API is unavailable."""
    avg_sold = ebay_comps.get("average_sold", 0)
    margin_target = config.get("business", {}).get("margin_target", 0.50)
    current_bid = listing.get("current_bid", 0)

    estimated_value = avg_sold if avg_sold > 0 else 0
    max_bid_price = round(estimated_value * margin_target, 2)
    deal_flag = current_bid < max_bid_price if max_bid_price > 0 else False

    return {
        "estimated_value": estimated_value,
        "max_bid_price": max_bid_price,
        "fb_marketplace_ceiling": round(estimated_value * 0.85, 2),
        "condition_score": "Fair",
        "condition_notes": "Condition could not be assessed (AI unavailable)",
        "deal_flag": deal_flag,
        "summary": f"Estimated value ${estimated_value:.2f} CAD based on eBay comps. "
                   f"{'Deal detected!' if deal_flag else 'Current bid exceeds target.'}",
    }


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
