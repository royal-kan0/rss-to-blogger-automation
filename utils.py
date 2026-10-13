"""
Utility helpers: safe JSON read/write, URL normalization, small delays.
"""
import json
import os
from urllib.parse import urlparse, urlunparse
import tempfile

def load_json_file(path, default=None):
    if default is None:
        default = {}
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json_file(path, obj):
    # atomic save
    dirn = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dirn)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def normalize_url(u: str) -> str:
    if not u:
        return ""
    try:
        p = urlparse(u)
        scheme = p.scheme or "https"
        netloc = p.netloc.lower()
        path = p.path.rstrip("/")
        # drop session ids, common tracking params? keep query by default but normalize order
        # For deduping, we remove common tracking params
        query = p.query
        # remove utm & fbclid etc
        if query:
            params = []
            for kv in query.split("&"):
                k = kv.split("=",1)[0].lower()
                if k.startswith("utm_") or k in ("fbclid","gclid","igshid"):
                    continue
                params.append(kv)
            query = "&".join(params)
        parts = (scheme, netloc, path, "", query, "")
        return urlunparse(parts)
    except Exception:
        return u

def safe_sleep(seconds: int):
    import time
    time.sleep(seconds)
