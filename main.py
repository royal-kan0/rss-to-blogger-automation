#!/usr/bin/env python3
"""
Orchestrator for RSS -> Blogger automation.
Reads RSS feeds, uses Playwright scraper, validates required fields
(title, description, image, video), publishes to Blogger when valid,
and manages posted/pending queues.
"""
import os
import time
import logging
from typing import List, Dict, Any
from utils import load_json_file, save_json_file, normalize_url, safe_sleep
from rss_utils import get_feed_entries, rss_entry_to_minimal
from scraper import Scraper
from blogger_client import BloggerClient

# Logging configuration
logging.basicConfig(format='[%(levelname)s] %(message)s', level=logging.INFO)
log = logging.getLogger("rss-to-blogger")

# Environment
BLOG_ID = os.getenv("BLOGGER_BLOG_ID")
CLIENT_ID = os.getenv("BLOGGER_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLOGGER_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("BLOGGER_REFRESH_TOKEN")
RSS_FEED_URLS = os.getenv("RSS_FEED_URLS", "")
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "10"))  # safety per run
POSTS_PER_DAY_LIMIT = int(os.getenv("POSTS_PER_DAY_LIMIT", "50"))  # optional daily cap

POSTED_FILE = "posted_urls.json"
PENDING_FILE = "pending_posts.json"

if not all([BLOG_ID, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, RSS_FEED_URLS]):
    log.warning("[CONFIG] One or more required environment variables are missing. "
                "Set BLOGGER_BLOG_ID, BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN, RSS_FEED_URLS")

def load_state():
    posted = load_json_file(POSTED_FILE, default=[])
    pending = load_json_file(PENDING_FILE, default=[])
    return posted, pending

def save_state(posted, pending):
    save_json_file(POSTED_FILE, posted)
    save_json_file(PENDING_FILE, pending)

def normalize_entry_identifier(entry: Dict[str, Any]) -> str:
    # prefer GUID, fallback to normalized link
    if entry.get("id"):
        return normalize_url(entry["id"])
    if entry.get("link"):
        return normalize_url(entry["link"])
    return ""

def build_work_queue(pending: List[Dict[str,Any]], new_entries: List[Dict[str,Any]], posted_ids:set) -> List[Dict[str,Any]]:
    # pending first, then new items that aren't already posted or pending
    queue = []
    seen = set()
    for p in pending:
        uid = normalize_entry_identifier(p)
        if uid and uid not in posted_ids:
            queue.append(p)
            seen.add(uid)
    for e in new_entries:
        uid = normalize_entry_identifier(e)
        if not uid:
            continue
        if uid in posted_ids or uid in seen:
            continue
        queue.append(e)
        seen.add(uid)
    return queue

def main():
    log.info("[RUN] Starting RSS to Blogger automation run")
    posted, pending = load_state()
    posted_set = set(posted)

    # Build list of feed entries
    feed_urls = [u.strip() for u in RSS_FEED_URLS.split(",") if u.strip()]
    log.info(f"[RSS] Feed URLs: {len(feed_urls)}")
    new_entries = []
    for feed_url in feed_urls:
        try:
            entries = get_feed_entries(feed_url)
            log.info(f"[RSS] {feed_url} -> {len(entries)} entries")
            for e in entries:
                new_entries.append(rss_entry_to_minimal(e))
        except Exception as ex:
            log.error(f"[RSS] Failed to parse {feed_url}: {ex}")

    work_queue = build_work_queue(pending, new_entries, posted_set)
    log.info(f"[QUEUE] Pending: {len(pending)} | New discovered: {len(new_entries)} | Work queue total: {len(work_queue)}")

    # Respect per-run publish cap
    publish_limit = min(MAX_POSTS_PER_RUN, POSTS_PER_DAY_LIMIT)
    log.info(f"[CONFIG] MAX_POSTS_PER_RUN={MAX_POSTS_PER_RUN}, effective publish_limit={publish_limit}")

    scraper = Scraper()
    blogger = BloggerClient(
        blog_id=BLOG_ID,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        refresh_token=REFRESH_TOKEN,
    )

    processed = 0
    new_pending = []  # updated pending queue to persist
    try:
        for item in work_queue:
            if processed >= publish_limit:
                log.info("[LIMIT] Reached per-run publish limit, moving remaining items to pending")
                # add remaining work_queue entries (including current) to new_pending
                idx = work_queue.index(item)
                new_pending.extend(work_queue[idx:])
                break

            uid = normalize_entry_identifier(item)
            log.info(f"[RSS] Processing: {item.get('link') or item.get('id')}")
            # Scrape page with Playwright for best data
            try:
                log.info("[BROWSER] Opening URL with Playwright")
                scraped = scraper.scrape(item.get("link") or item.get("id"))
                log.info(f"[SCRAPER] Title: {scraped.get('title')}, Image: {bool(scraped.get('image'))}, Video: {bool(scraped.get('video'))}")
            except Exception as ex:
                log.error(f"[BROWSER] Scrape failed for {item.get('link')}: {ex}")
                # fallback to RSS metadata
                scraped = {
                    "title": item.get("title"),
                    "description": item.get("description"),
                    "image": item.get("image"),
                    "video": item.get("video"),
                    "source": "rss-fallback",
                    "url": item.get("link")
                }

            # Combine & fallback priority managed in scraper/rss_utils -> ensure fields exist
            title = scraped.get("title") or item.get("title")
            description = scraped.get("description") or item.get("description")
            image = scraped.get("image")
            video = scraped.get("video")

            # Validate mandatory fields
            missing = []
            if not title:
                missing.append("title")
            if not description:
                missing.append("description")
            if not image:
                log.info("[VALIDATION] Image not found in primary scraped sources; will try RSS fallback")
                image = image or item.get("image")
            if not video:
                log.info("[VALIDATION] Video not found; will try RSS fallback")
                video = video or item.get("video")

            if not video:
                log.warning("[VALIDATION] Video not found -> moving to pending queue")
                item.setdefault("reasons", []).append("video_missing")
                new_pending.append(item)
                continue

            # Build HTML content
            html = blogger.build_post_html(
                title=title,
                description=description or "",
                image_url=image,
                video=video,
                source_url=item.get("link") or item.get("id")
            )

            log.info("[VALIDATION] Required data found, publishing to Blogger")
            try:
                resp = blogger.publish_post(title=title, content_html=html)
            except blogger.RateLimitError as rl:
                log.error("[BLOGGER] Rate limit / quota error encountered; preserving remaining posts to pending and stopping")
                # move current and remaining to pending
                item.setdefault("reasons", []).append("quota_exceeded")
                new_pending.append(item)
                idx = work_queue.index(item)
                new_pending.extend(work_queue[idx+1:])
                break
            except Exception as ex:
                log.error(f"[BLOGGER] Publish failed: {ex}")
                item.setdefault("reasons", []).append("publish_failed")
                new_pending.append(item)
                continue

            # On success
            log.info(f"[BLOGGER] Publish successful: {resp.get('id') if isinstance(resp, dict) else resp}")
            posted.append(uid)
            save_state(posted, new_pending + [p for p in work_queue if normalize_entry_identifier(p) in [normalize_entry_identifier(x) for x in new_pending]])
            processed += 1
            safe_sleep(2)  # small delay between publishes to be polite

    finally:
        # Save final state (merge new_pending with any leftover pending that weren't processed)
        merged_pending = []
        # keep existing pending items (that were not processed) plus new_pending
        existing_pending_ids = {normalize_entry_identifier(p) for p in pending}
        for p in pending:
            if normalize_entry_identifier(p) not in posted:
                merged_pending.append(p)
        # add newly created pending (deduplicated)
        seen = {normalize_entry_identifier(p) for p in merged_pending}
        for p in new_pending:
            key = normalize_entry_identifier(p)
            if key not in seen and key not in posted:
                merged_pending.append(p)
                seen.add(key)

        save_state(posted, merged_pending)
        log.info(f"[RUN] Completed. Published: {processed}. Posted total: {len(posted)}. Pending: {len(merged_pending)}")

if __name__ == "__main__":
    main()
