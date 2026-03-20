"""
Slack and Discord webhook notifications for camera deal alerts.
Configure webhook URLs in config.yaml under the 'webhooks' section.
"""

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


def _format_listing_text(listing, research: dict, ending_soon: bool = False) -> dict:
    """Build structured message data from a listing + research."""
    title = listing.title if hasattr(listing, "title") else listing.get("title", "Unknown")
    current_bid = listing.current_bid if hasattr(listing, "current_bid") else listing.get("current_bid", 0)
    maxsold_url = listing.maxsold_url if hasattr(listing, "maxsold_url") else listing.get("maxsold_url", "#")
    end_time = listing.auction_end_time if hasattr(listing, "auction_end_time") else listing.get("auction_end_time")
    photo_urls = listing.photo_urls if hasattr(listing, "photo_urls") else listing.get("photo_urls", [])

    est_value = research.get("estimated_value", 0)
    max_bid = research.get("max_bid_price", 0)
    condition = research.get("condition_score", "?")
    deal = research.get("deal_flag", False)

    end_str = ""
    if end_time:
        if isinstance(end_time, datetime):
            end_str = end_time.strftime("%b %d, %I:%M %p")
        else:
            end_str = str(end_time)

    photo_url = ""
    if photo_urls and isinstance(photo_urls, list) and photo_urls:
        photo_url = photo_urls[0]

    return {
        "title": title,
        "current_bid": current_bid,
        "est_value": est_value,
        "max_bid": max_bid,
        "condition": condition,
        "deal": deal,
        "end_str": end_str,
        "url": maxsold_url,
        "photo_url": photo_url,
        "ending_soon": ending_soon,
    }


def send_slack_webhook(listing, research: dict, config: dict, ending_soon: bool = False) -> bool:
    """Send a Slack webhook notification."""
    webhook_url = config.get("webhooks", {}).get("slack_url", "")
    if not webhook_url:
        return False

    data = _format_listing_text(listing, research, ending_soon)

    urgency = ":rotating_light: *ENDING SOON* " if data["ending_soon"] else ""
    deal_emoji = ":white_check_mark: DEAL" if data["deal"] else ""

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{'ENDING SOON - ' if data['ending_soon'] else ''}Camera Alert: {data['title']}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Current Bid:* ${data['current_bid']:.2f} CAD"},
                    {"type": "mrkdwn", "text": f"*Est. Value:* ${data['est_value']:.2f} CAD"},
                    {"type": "mrkdwn", "text": f"*Max Bid:* ${data['max_bid']:.2f} CAD"},
                    {"type": "mrkdwn", "text": f"*Condition:* {data['condition']}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{urgency}{deal_emoji}\n{'Ends: ' + data['end_str'] if data['end_str'] else ''}\n<{data['url']}|View on Maxsold>",
                },
            },
        ],
    }

    # Add photo if available
    if data["photo_url"]:
        payload["blocks"].insert(2, {
            "type": "image",
            "image_url": data["photo_url"],
            "alt_text": data["title"],
        })

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"Slack webhook sent: {data['title']}")
        return True
    except Exception as e:
        logger.warning(f"Slack webhook failed: {e}")
        return False


def send_discord_webhook(listing, research: dict, config: dict, ending_soon: bool = False) -> bool:
    """Send a Discord webhook notification."""
    webhook_url = config.get("webhooks", {}).get("discord_url", "")
    if not webhook_url:
        return False

    data = _format_listing_text(listing, research, ending_soon)

    urgency_color = 0xFF0000 if data["ending_soon"] else (0x28A745 if data["deal"] else 0x339AF0)

    embed = {
        "title": f"{'ENDING SOON - ' if data['ending_soon'] else ''}Camera Alert: {data['title']}",
        "url": data["url"],
        "color": urgency_color,
        "fields": [
            {"name": "Current Bid", "value": f"${data['current_bid']:.2f} CAD", "inline": True},
            {"name": "Est. Value", "value": f"${data['est_value']:.2f} CAD", "inline": True},
            {"name": "Max Bid", "value": f"${data['max_bid']:.2f} CAD", "inline": True},
            {"name": "Condition", "value": data["condition"], "inline": True},
            {"name": "Deal?", "value": "YES" if data["deal"] else "No", "inline": True},
        ],
        "footer": {"text": "Camera Finder — Vancouver"},
    }

    if data["end_str"]:
        embed["fields"].append({"name": "Auction Ends", "value": data["end_str"], "inline": True})

    if data["photo_url"]:
        embed["thumbnail"] = {"url": data["photo_url"]}

    payload = {"embeds": [embed]}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"Discord webhook sent: {data['title']}")
        return True
    except Exception as e:
        logger.warning(f"Discord webhook failed: {e}")
        return False


def send_webhooks(listing, research: dict, config: dict, ending_soon: bool = False) -> bool:
    """Send to all configured webhooks (Slack and/or Discord). Returns True if any succeeded."""
    sent = False
    if config.get("webhooks", {}).get("slack_url"):
        sent = send_slack_webhook(listing, research, config, ending_soon) or sent
    if config.get("webhooks", {}).get("discord_url"):
        sent = send_discord_webhook(listing, research, config, ending_soon) or sent
    return sent
