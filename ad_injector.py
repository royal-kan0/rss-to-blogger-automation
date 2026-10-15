"""
Insert ad snippets into HTML content between paragraphs.
Usage: inject_ads_into_content(html, ad_snippets:list, interval=3)
"""
from bs4 import BeautifulSoup
from typing import List

def inject_ads_into_content(html: str, ad_snippets: List[str], interval: int = 4) -> str:
    if not ad_snippets:
        return html
    soup = BeautifulSoup(html, "lxml")
    paragraphs = soup.find_all(["p","h2","h3","figure","div"])
    if not paragraphs:
        return html
    ad_index = 0
    inserted = 0
    for i, p in enumerate(paragraphs):
        if (i+1) % interval == 0 and ad_index < len(ad_snippets):
            wrapper = soup.new_tag("div")
            wrapper.attrs["class"] = "injected-ad"
            wrapper.append(BeautifulSoup(ad_snippets[ad_index], "html.parser"))
            p.insert_after(wrapper)
            ad_index += 1
            inserted += 1
    return str(soup)
