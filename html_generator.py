"""
Generate responsive blogger-compatible HTML from extracted data.
"""
from typing import List, Optional
from ad_injector import inject_ads_into_content
import html as _html

def generate_post_html(title: str, description: str, main_content_html: str, selected_image: Optional[str], video_html: Optional[str], source_url: Optional[str], ad_snippets: List[str]=None) -> str:
    ad_snippets = ad_snippets or []
    parts = []
    # intro
    parts.append(f'<div class="post-intro"><h1>{_html.escape(title)}</h1>')
    if description:
        parts.append(f'<p>{_html.escape(description)}</p>')
    parts.append('</div>')
    if selected_image:
        parts.append(f'<div class="featured-image"><img src="{selected_image}" alt="{_html.escape(title)}" style="max-width:100%;height:auto;display:block;margin:12px auto;" loading="lazy" /></div>')
    # insert ad top if present
    if ad_snippets:
        parts.append(f'<div class="ad-top">{ad_snippets[0]}</div>')
    # main content (sanitize assumed done earlier)
    content_with_ads = inject_ads_into_content(main_content_html, ad_snippets[1:], interval=4)
    parts.append(f'<div class="post-content">{content_with_ads}</div>')
    # video instruction box + video
    if video_html:
        parts.append('<div style="margin:16px 0;padding:12px;border-radius:8px;background:linear-gradient(90deg,#fff,#f4f7fb);">👇 वीडियो देखने के लिए नीचे स्क्रॉल करें।</div>')
        parts.append(video_html)
    # source
    if source_url:
        parts.append(f'<p style="font-size:0.9em;color:#666">Source: <a href="{source_url}">{source_url}</a></p>')
    # join
    return "\n".join(parts)
