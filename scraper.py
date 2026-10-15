"""
Playwright-based scraper with robust media extraction and smart scrolling.
Provides a simple sync API: Scraper.scrape(url) -> dict with title, description,
images (candidates), selected_image, videos (candidates), selected_video,
raw_html, final_url, canonical, http_status, json_ld, meta.
"""
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
import re
import json
import logging
from urllib.parse import urljoin
from typing import List

log = logging.getLogger("scraper")

def extract_json_ld(soup: BeautifulSoup):
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

def _collect_media_from_dom(soup: BeautifulSoup, base_url: str):
    imgs = []
    for img in soup.find_all("img"):
        for attr in ("src", "currentSrc", "data-src", "data-lazy-src", "data-original", "data-srcset", "srcset"):
            val = img.get(attr)
            if val:
                if attr in ("data-srcset", "srcset"):
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
    vids = []
    for video in soup.find_all("video"):
        s = video.get("src")
        if s:
            vids.append(urljoin(base_url, s))
        for src in video.find_all("source"):
            v = src.get("src")
            if v:
                vids.append(urljoin(base_url, v))
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
            for m in re.findall(r'(https?:\/\/[^\s\'"]+\.(?:mp4|m3u8|webm|m4v|ogg))', txt):
                script_media.append(m)
    return {
        "images": imgs,
        "videos": vids,
        "iframes": iframes,
        "script_media": script_media
    }

class Scraper:
    def __init__(self, headless=True, timeout=30000, user_agent=None):
        self.headless = headless
        self.timeout = timeout
        self.user_agent = user_agent or "Mozilla/5.0 (X11; Linux x86_64) rss-to-blogger-bot"

    def _scroll_page(self, page):
        try:
            page_height = page.evaluate("() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)")
            viewport_height = page.viewport_size["height"] if page.viewport_size else 800
            cur = 0
            steps = 6
            for i in range(steps):
                cur = int((i+1) * (page_height/steps))
                page.evaluate("(y) => window.scrollTo(0, y)", cur)
                page.wait_for_timeout(500)
        except Exception:
            # non-fatal
            pass

    def _try_accept_cookie(self, page):
        # Try common accept buttons (non-intrusive)
        try:
            buttons = page.query_selector_all("button, a, input[type=button]")
            for b in buttons:
                txt = (b.inner_text() or "").strip().lower()
                if any(k in txt for k in ("accept", "agree", "i agree", "allow", "ok", "continue")) and len(txt) < 30:
                    try:
                        b.click(timeout=2000)
                        page.wait_for_timeout(500)
                        return True
                    except Exception:
                        continue
        except Exception:
            pass
        return False

    def scrape(self, url: str) -> dict:
        log.info(f"[BROWSER] Scrape start: {url}")
        final_html = ""
        final_url = url
        canonical = None
        status = None
        json_ld = []
        meta = {}
        js_collected = {"imgs": [], "videos": [], "iframes": []}

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(user_agent=self.user_agent)
            page = context.new_page()
            response = None
            try:
                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
                    # try to accept cookie dialogs if present
                    self._try_accept_cookie(page)
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except PlaywrightTimeoutError:
                        pass
                    self._scroll_page(page)
                    page.wait_for_timeout(800)
                    final_html = page.content()
                    final_url = page.url
                    status = response.status if response else None

                    # collect JS-level attributes safely
                    js_collected = page.evaluate("""() => {
                        const imgs = Array.from(document.querySelectorAll('img')).map(i => ({
                            src: i.getAttribute('src'),
                            currentSrc: i.currentSrc || i.getAttribute('src'),
                            data_src: i.getAttribute('data-src'),
                            data_lazy: i.getAttribute('data-lazy-src'),
                            data_original: i.getAttribute('data-original'),
                            srcset: i.getAttribute('srcset')
                        }));
                        const vids = Array.from(document.querySelectorAll('video, source')).map(v => {
                            return v.getAttribute && (v.getAttribute('src') || v.getAttribute('data-src') || v.getAttribute('data-video') || v.getAttribute('data-src'));
                        }).filter(Boolean);
                        const iframes = Array.from(document.querySelectorAll('iframe')).map(i => i.getAttribute('src')).filter(Boolean);
                        return {imgs, videos: vids, iframes};
                    }""")
                except PlaywrightTimeoutError as e:
                    log.warning(f"[BROWSER] Navigation timeout for {url}: {e}")
                    # still attempt to get whatever is available
                    try:
                        final_html = page.content()
                        final_url = page.url
                    except Exception:
                        final_html = ""
                except Exception as e:
                    log.error(f"[BROWSER] Unexpected error while navigating {url}: {e}")
                    raise
            finally:
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass

        soup = BeautifulSoup(final_html or "", "lxml")

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

        # canonical
        canon_tag = soup.find("link", rel="canonical")
        if canon_tag and canon_tag.get("href"):
            canonical = urljoin(final_url, canon_tag["href"])

        json_ld = extract_json_ld(soup)
        collected = _collect_media_from_dom(soup, final_url)

        # include JS-collected results (if any)
        if js_collected:
            for i in js_collected.get("imgs", []):
                if not i:
                    continue
                val = i.get("currentSrc") or i.get("src") or i.get("data_src") or i.get("data_lazy")
                if val:
                    collected["images"].append(urljoin(final_url, val))
            for v in js_collected.get("videos", []):
                collected["videos"].append(urljoin(final_url, v))
            for ifr in js_collected.get("iframes", []):
                collected["iframes"].append(urljoin(final_url, ifr))

        # normalize candidates & pick best helpers
        def pick_best_image(imgs: List[str]):
            clean = []
            for i in imgs:
                try:
                    if not i:
                        continue
                    low = i.lower()
                    if low.endswith((".ico", ".svg")): continue
                    if re.search(r'1x1|pixel|spacer', low): continue
                    clean.append(i)
                except Exception:
                    continue
            seen = set()
            for c in clean:
                if c not in seen:
                    seen.add(c)
                    return urljoin(final_url, c)
            return None

        def pick_best_video(videos: List[str], script_media: List[str], iframes: List[str], meta: dict):
            for v in (videos + script_media):
                if not v: continue
                if any(ext in v for ext in [".mp4", ".webm", ".m4v", ".ogg", ".m3u8"]):
                    return urljoin(final_url, v)
            if meta.get("og:video"):
                return urljoin(final_url, meta["og:video"])
            # check json-ld objects
            for obj in json_ld:
                try:
                    if isinstance(obj, dict):
                        if obj.get("contentUrl"):
                            return urljoin(final_url, obj["contentUrl"])
                        if obj.get("embedUrl"):
                            return urljoin(final_url, obj["embedUrl"])
                        if obj.get("video") and isinstance(obj["video"], dict) and obj["video"].get("contentUrl"):
                            return urljoin(final_url, obj["video"]["contentUrl"])
                except Exception:
                    continue
            # iframe providers recognized
            for ifr in iframes:
                if any(host in ifr for host in ("youtube.com","youtu.be","vimeo.com","dailymotion")):
                    return ifr
            return None

        image = pick_best_image(collected["images"] + ([meta.get("og:image")] if meta.get("og:image") else []))
        video = pick_best_video(collected["videos"], collected["script_media"], collected["iframes"], meta)

        # title
        title = None
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            title = h1.get_text(strip=True)
        if not title:
            title = meta.get("og:title") or (json_ld[0].get("headline") if json_ld and isinstance(json_ld[0], dict) and json_ld[0].get("headline") else None)
        if not title:
            t = soup.find("title")
            if t:
                title = t.get_text(strip=True)

        # description
        description = meta.get("og:description") or (soup.find("meta", {"name":"description"}) or {}).get("content")
        if not description:
            for p in soup.find_all("p"):
                txt = p.get_text(strip=True)
                if txt and len(txt) > 50:
                    description = txt
                    break

        return {
            "title": title,
            "description": description,
            "images": collected["images"],
            "image": image,
            "videos": collected["videos"] + collected["script_media"],
            "video": video,
            "iframes": collected["iframes"],
            "raw_html": final_html,
            "json_ld": json_ld,
            "meta": meta,
            "final_url": final_url,
            "canonical": canonical,
            "http_status": status
                    }
