"""
RSS parsing utilities. Uses feedparser to extract common media metadata
and returns a normalized minimal dict for the orchestrator.
"""
import feedparser
import logging
from typing import Dict, Any
from urllib.parse import urljoin

log = logging.getLogger("rss_utils")

def get_feed_entries(feed_url: str):
    d = feedparser.parse(feed_url)
    if d.bozo:
        log.warning(f"[RSS] feedparser bozo: {d.bozo_exception} for {feed_url}")
    return d.entries or []

def extract_media_from_entry(entry) -> Dict[str, Any]:
    media = {
        "title": entry.get("title"),
        "link": entry.get("link") or (entry.get("links")[0].get("href") if entry.get("links") else None),
        "description": entry.get("description") or entry.get("summary"),
        "content": None,
        "image": None,
        "video": None,
        "guid": entry.get("id") or entry.get("guid")
    }
    if entry.get("content"):
        media["content"] = entry["content"][0].get("value")
    enclosures = entry.get("enclosures") or []
    for e in enclosures:
        t = e.get("type","")
        href = e.get("href")
        if not href:
            continue
        if "video" in t or any(ext in href for ext in [".mp4",".webm",".m3u8",".m4v",".ogg"]):
            media["video"] = media["video"] or href
        if "image" in t or any(href.lower().endswith(ext) for ext in [".jpg",".jpeg",".png",".webp",".gif"]):
            media["image"] = media["image"] or href

    media_content = entry.get("media_content") or []
    for mc in media_content:
        url = mc.get("url")
        if url:
            if mc.get("medium") == "image" or any(url.lower().endswith(ext) for ext in [".jpg",".png",".webp"]):
                media["image"] = media["image"] or url
            if mc.get("medium") == "video" or any(url.lower().endswith(ext) for ext in [".mp4",".webm",".m3u8"]):
                media["video"] = media["video"] or url

    media_thumbnail = entry.get("media_thumbnail") or []
    if media_thumbnail and not media["image"]:
        media["image"] = media_thumbnail[0].get("url")

    # if link is relative, try to resolve using feed's base if present in entry
    if media["link"] and entry.get("href"):
        media["link"] = urljoin(entry.get("href"), media["link"])

    return media

def rss_entry_to_minimal(entry) -> Dict[str, Any]:
    media = extract_media_from_entry(entry)
    return {
        "id": media.get("guid") or entry.get("id") or entry.get("link"),
        "title": media.get("title") or entry.get("title"),
        "description": media.get("description") or entry.get("summary"),
        "image": media.get("image"),
        "video": media.get("video"),
        "link": media.get("link") or entry.get("link"),
        "raw_entry": entry
    }
