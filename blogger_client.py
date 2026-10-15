"""
Simple Blogger client using refresh token -> access token flow + REST API.
Handles rate-limit detection and provides publish_post(title, content_html).
"""
import requests
import logging
from typing import Dict, Any
from pathlib import Path
import html as _html

log = logging.getLogger("blogger_client")

TOKEN_URL = "https://oauth2.googleapis.com/token"
BLOGGER_POSTS_URL = "https://www.googleapis.com/blogger/v3/blogs/{blogId}/posts/"

class RateLimitError(Exception):
    pass

class BloggerClient:
    def __init__(self, blog_id: str, client_id: str, client_secret: str, refresh_token: str):
        self.blog_id = blog_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token

    def _get_access_token(self) -> str:
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token"
        }
        r = requests.post(TOKEN_URL, data=data, timeout=15)
        if r.status_code != 200:
            log.error(f"[BLOGGER] Token exchange failed: {r.status_code} {r.text}")
            raise Exception("TokenExchangeFailed")
        return r.json().get("access_token")

    def build_post_html(self, title: str, description: str, content_html: str, image_url: str = None, video_html: str = None, source_url: str = None, ad_top: str = "", ad_inline: str = "") -> str:
        tpl_path = Path("templates/blogger_template.html")
        if tpl_path.exists():
            tpl = tpl_path.read_text(encoding="utf-8")
            # placeholders in template: {{TITLE}}, {{DESCRIPTION}}, {{CONTENT}}, {{IMAGE_URL}}, {{VIDEO_HTML}}, {{SOURCE_URL}}, {{AD_TOP}}, {{AD_INLINE}}
            return (tpl.replace("{{TITLE}}", self._escape(title))
                       .replace("{{DESCRIPTION}}", description or "")
                       .replace("{{CONTENT}}", content_html or "")
                       .replace("{{IMAGE_URL}}", image_url or "")
                       .replace("{{VIDEO_HTML}}", video_html or "")
                       .replace("{{SOURCE_URL}}", source_url or "")
                       .replace("{{AD_TOP}}", ad_top or "")
                       .replace("{{AD_INLINE}}", ad_inline or ""))
        # fallback minimal HTML
        parts = []
        parts.append(f'<h1>{self._escape(title)}</h1>')
        if description:
            parts.append(f'<p>{_html.escape(description)}</p>')
        if image_url:
            parts.append(f'<img src="{image_url}" alt="{self._escape(title)}" style="max-width:100%;height:auto;display:block;margin:15px auto;" loading="lazy" />')
        if video_html:
            parts.append(video_html)
        if content_html:
            parts.append(content_html)
        if source_url:
            parts.append(f'<p><a href="{source_url}">Source</a></p>')
        return "\n".join(parts)

    def _escape(self, text: str) -> str:
        return _html.escape(text or "")

    def _video_embed_html(self, video_url: str) -> str:
        if not video_url:
            return ""
        # direct file
        if any(ext in video_url for ext in [".mp4", ".webm", ".m4v", ".ogg"]):
            mime = "video/mp4" if ".mp4" in video_url else "video/webm" if ".webm" in video_url else ""
            return f'<video controls playsinline preload="metadata" style="max-width:100%;height:auto;"><source src="{video_url}" type="{mime}">Your browser does not support the video tag.</video>'
        if ".m3u8" in video_url:
            return (
                f'<div style="position:relative;padding-top:56.25%"><video id="player" controls playsinline preload="metadata" style="position:absolute;top:0;left:0;width:100%;height:100%;"></video></div>'
                f'<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>'
                f'<script>'
                f'if (window.Hls && Hls.isSupported()) {{'
                f'  var hls = new Hls(); hls.loadSource("{video_url}"); hls.attachMedia(document.getElementById("player"));'
                f'}} else if (document.getElementById("player").canPlayType("application/vnd.apple.mpegurl")) {{'
                f'  document.getElementById("player").src = "{video_url}";'
                f'}}'
                f'</script>'
            )
        if "youtube.com" in video_url or "youtu.be" in video_url:
            vid = ""
            if "watch?v=" in video_url:
                vid = video_url.split("v=")[-1].split("&")[0]
            elif "youtu.be/" in video_url:
                vid = video_url.rsplit("/",1)[-1].split("?")[0]
            if vid:
                return f'<div style="position:relative;padding-top:56.25%"><iframe src="https://www.youtube.com/embed/{vid}" style="position:absolute;top:0;left:0;width:100%;height:100%;" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe></div>'
        # fallback iframe
        return f'<div style="position:relative;padding-top:56.25%"><iframe src="{video_url}" style="position:absolute;top:0;left:0;width:100%;height:100%;" frameborder="0" allowfullscreen></iframe></div>'

    def publish_post(self, title: str, content_html: str) -> Dict[str, Any]:
        token = self._get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = BLOGGER_POSTS_URL.format(blogId=self.blog_id)
        payload = {
            "kind": "blogger#post",
            "title": title,
            "content": content_html
        }
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        if r.status_code in (429, 403) or (r.status_code >= 400 and "quota" in (r.text or "").lower()):
            log.error(f"[BLOGGER] Rate/quota error: {r.status_code} {r.text}")
            raise RateLimitError(r.text)
        if r.status_code >= 400:
            log.error(f"[BLOGGER] Publish error: {r.status_code} {r.text}")
            raise Exception(f"PublishFailed: {r.status_code}")
        return r.json()
