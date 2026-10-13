"""
Simple Blogger client using refresh token -> access token flow + REST API.
Handles rate-limit detection and provides a publish_post(title, content_html) method.
"""
import requests
import logging
from typing import Dict, Any

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

    def build_post_html(self, title: str, description: str, image_url: str, video: str, source_url: str) -> str:
        # Uses an external template file (template string inserted here)
        # For brevity embed the responsive HTML template (you can keep it in templates/ as well)
        from pathlib import Path
        tpl_path = Path("templates/blogger_template.html")
        if tpl_path.exists():
            tpl = tpl_path.read_text(encoding="utf-8")
            return tpl.replace("{{TITLE}}", self._escape(title)).replace("{{DESCRIPTION}}", description or "").replace("{{IMAGE_URL}}", image_url or "").replace("{{VIDEO_URL}}", video or "").replace("{{SOURCE_URL}}", source_url or "")
        # fallback minimal HTML
        html = f"""
        <h1>{self._escape(title)}</h1>
        <p>{description or ''}</p>
        {'<img src="'+image_url+'" alt="'+self._escape(title)+'" style="max-width:100%;height:auto;" />' if image_url else ''}
        <div>{self._video_embed_html(video)}</div>
        <p><a href="{source_url}">Source</a></p>
        """
        return html

    def _escape(self, text: str) -> str:
        import html
        return html.escape(text or "")

    def _video_embed_html(self, video_url: str) -> str:
        if not video_url:
            return ""
        # direct file
        if any(ext in video_url for ext in [".mp4", ".webm", ".m4v", ".ogg"]):
            # ensure quotes and & are handled in HTML
            return f'<video controls playsinline preload="metadata" style="max-width:100%;height:auto;"><source src="{video_url}" type="video/mp4">Your browser does not support the video tag.</video>'
        # HLS .m3u8 -> use simple video tag with hls.js fallback instruction (we keep simple embed)
        if ".m3u8" in video_url:
            # include a minimal HLS player embed using hls.js CDN (works on browsers supporting MSE)
            return (
                f'<video id="player" controls playsinline preload="metadata" style="max-width:100%;height:auto;"></video>'
                f'<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>'
                f'<script>'
                f'if(Hls.isSupported()) {{'
                f'  var hls = new Hls(); hls.loadSource("{video_url}"); hls.attachMedia(document.getElementById("player"));'
                f'}} else if (document.getElementById("player").canPlayType("application/vnd.apple.mpegurl")) {{'
                f'  document.getElementById("player").src = "{video_url}";'
                f'}}'
                f'</script>'
            )
        # iframe providers (youtube,vimeo)
        if "youtube.com" in video_url or "youtu.be" in video_url:
            # normalize to embed
            if "watch?v=" in video_url:
                vid = video_url.split("v=")[-1].split("&")[0]
            elif "youtu.be/" in video_url:
                vid = video_url.rsplit("/",1)[-1].split("?")[0]
            else:
                vid = ""
            if vid:
                return f'<iframe src="https://www.youtube.com/embed/{vid}" width="100%" height="360" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>'
        # fallback to raw iframe
        return f'<iframe src="{video_url}" width="100%" height="360" frameborder="0" allowfullscreen></iframe>'

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
