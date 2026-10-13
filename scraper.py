"""
Playwright-based scraper with robust media extraction and smart scrolling.
Provides a simple sync API: Scraper.scrape(url) -> dict with title, description, image, video, raw_html.
"""
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
import re
import json
import logging
from urllib.parse import urljoin

log = logging.getLogger("scraper")

def extract_json_ld(soup):
    results = []
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            text = tag.string
            if not text:
                continue
            data = json.loads(text)
            results.append(data)
        except Exception:
            continue
    return results

def _collect_media_from_dom(soup, base_url):
    # images
    imgs = []
    for img in soup.find_all("img"):
        for attr in ("src", "data-src", "data-lazy-src", "data-original", "data-srcset"):
            val = img.get(attr)
            if val:
                # handle srcset
                if attr == "data-srcset" or "srcset" in attr or attr == "srcset":
                    parts = [p.strip().split(" ")[0] for p in val.split(",")]
                    imgs.extend([urljoin(base_url, p) for p in parts if p])
                else:
                    imgs.append(urljoin(base_url, val))
    # CSS background-image
    for el in soup.find_all(style=True):
        m = re.search(r'background(?:-image)?:\s*url\(([^)]+)\)', el['style'])
        if m:
            url = m.group(1).strip(' "\'')
            imgs.append(urljoin(base_url, url))
    # videos
    vids = []
    for video in soup.find_all("video"):
        s = video.get("src")
        if s:
            vids.append(urljoin(base_url, s))
        for src in video.find_all("source"):
            v = src.get("src")
            if v:
                vids.append(urljoin(base_url, v))
    # iframes might be embed players (youtube, vimeo)
    iframes = []
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src")
        if src:
            iframes.append(urljoin(base_url, src))
    # scripts scanning for .mp4/.m3u8 etc
    script_media = []
    for s in soup.find_all("script"):
        txt = s.string or ""
        if ".mp4" in txt or ".m3u8" in txt or ".webm" in txt:
            # naive regex for urls
            for m in re.findall(r'(https?:\/\/[^\s\'"]+\.(?:mp4|m3u8|webm|m4v|ogg))', txt):
                script_media.append(m)
    return {
        "images": imgs,
        "videos": vids,
        "iframes": iframes,
        "script_media": script_media
    }

class Scraper:
    def __init__(self, headless=True, timeout=30000):
        self.headless = headless
        self.timeout = timeout

    def _scroll_page(self, page):
        # progressively scroll down to trigger lazy-load
        page_height = page.evaluate("() => document.body.scrollHeight")
        viewport_height = page.viewport_size["height"] if page.viewport_size else 800
        cur = 0
        steps = 6
        for i in range(steps):
            cur = int((i+1) * (page_height/steps))
            page.evaluate("(y) => window.scrollTo(0, y)", cur)
            page.wait_for_timeout(500)

    def scrape(self, url: str) -> dict:
        log.info(f"[BROWSER] Scrape start: {url}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) rss-to-blogger-bot")
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
                # wait for network idle briefly
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except PlaywrightTimeoutError:
                    pass
                # small scroll to trigger lazy loads
                self._scroll_page(page)
                # give time for lazy-loading
                page.wait_for_timeout(1000)
                html = page.content()
                # run JS to collect media-friendly attributes (data-*)
                js_result = page.evaluate("""
                () => {
                    function collect() {
                        const imgs = Array.from(document.querySelectorAll('img')).map(i => {
                            return {
                                src: i.getAttribute('src'),
                                data_src: i.getAttribute('data-src'),
                                data_lazy: i.getAttribute('data-lazy-src'),
                                data_original: i.getAttribute('data-original'),
                                srcset: i.getAttribute('srcset')
                            };
                        });
                        const videos = Array.from(document.querySelectorAll('video, source')).map(v => v.getAttribute('src') || v.getAttribute('data-src') || v.getAttribute('data-video') || v.getAttribute('data-video-url'));
                        const iframes = Array.from(document.querySelectorAll('iframe')).map(i => i.getAttribute('src'));
                        return {imgs, videos, iframes};
                    }
                    return collect();
                }
                """)
            finally:
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass

        soup = BeautifulSoup(html, "lxml")
        # gather candidates
        meta = {}
        # meta tags
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            meta["og:title"] = og_title["content"]
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            meta["og:description"] = og_desc["content"]
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            meta["og:image"] = og_image["content"]
        og_video = soup.find("meta", property="og:video")
        if og_video and og_video.get("content"):
            meta["og:video"] = og_video["content"]

        # json-ld
        ld = extract_json_ld(soup)

        collected = _collect_media_from_dom(soup, url)
        # include JS-collected results
        if js_result:
            for i in js_result.get("imgs", []):
                if i:
                    collected["images"].append(i.get("src") or i.get("data_src") or i.get("data_lazy") or i.get("data_original") or "")
            for v in js_result.get("videos", []):
                if v:
                    collected["videos"].append(v)
            for ifr in js_result.get("iframes", []):
                if ifr:
                    collected["iframes"].append(ifr)

        # normalize and pick best candidates (simple heuristic: exclude tiny/tracking images)
        def pick_best_image(imgs):
            clean = []
            for i in imgs:
                try:
                    if not i:
                        continue
                    if i.lower().endswith(("ico","svg")):
                        continue
                    if re.search(r'1x1|pixel|spacer', i):
                        continue
                    clean.append(i)
                except Exception:
                    continue
            # return first unique
            seen = set()
            for c in clean:
                if c not in seen:
                    seen.add(c)
                    return urljoin(url, c)
            return None

        def pick_best_video(videos, script_media, iframes, meta):
            # prefer direct mp4/webm/etc
            for v in videos + script_media:
                if not v:
                    continue
                if any(ext in v for ext in [".mp4", ".webm", ".m4v", ".ogg", ".m3u8"]):
                    return urljoin(url, v)
            # og:video
            if meta.get("og:video"):
                return urljoin(url, meta["og:video"])
            # json-ld contentUrl/embedUrl
            for obj in ld:
                try:
                    if isinstance(obj, dict):
                        if obj.get("contentUrl"):
                            return urljoin(url, obj["contentUrl"])
                        if obj.get("embedUrl"):
                            return urljoin(url, obj["embedUrl"])
                        if obj.get("video") and isinstance(obj["video"], dict) and obj["video"].get("contentUrl"):
                            return urljoin(url, obj["video"]["contentUrl"])
                except Exception:
                    continue
            # iframe: may be youtube/vimeo
            for ifr in iframes:
                if "youtube.com" in ifr or "youtu.be" in ifr or "vimeo.com" in ifr or "dailymotion" in ifr:
                    return ifr
            return None

        image = pick_best_image(collected["images"] + ([meta.get("og:image")] if meta.get("og:image") else []))
        video = pick_best_video(collected["videos"], collected["script_media"], collected["iframes"], meta)

        # title: h1 > og > json-ld > title
        title = None
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            title = h1.get_text(strip=True)
        if not title:
            title = meta.get("og:title") or (ld[0].get("headline") if ld and isinstance(ld[0], dict) and ld[0].get("headline") else None)
        if not title:
            t = soup.find("title")
            if t:
                title = t.get_text(strip=True)

        # description: meta og:description > meta description > article text
        description = meta.get("og:description") or (soup.find("meta", {"name":"description"}) or {}).get("content")
        if not description:
            # attempt to extract first meaningful paragraph
            for p in soup.find_all("p"):
                txt = p.get_text(strip=True)
                if txt and len(txt) > 50:
                    description = txt
                    break

        return {
            "title": title,
            "description": description,
            "image": image,
            "video": video,
            "raw_html": html,
            "json_ld": ld,
            "meta": meta
              }
