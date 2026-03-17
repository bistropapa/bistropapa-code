#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

OUTPUT_FILE = Path("news.json")

def fetch_rss(query):
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=ja&gl=JP&ceid=JP:ja"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as res:
        return res.read()

def parse_rss(xml):
    root = ET.fromstring(xml)
    items = []
    for item in root.findall(".//item")[:5]:
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        source = item.findtext("source", "")
        items.append({
            "title": title,
            "url": link,
            "source": source,
            "publishedAt": ""
        })
    return items

def main():
    queries = [
        "料理 レシピ 食育",
        "育児 パパ 子育て",
        "AI ChatGPT DX"
    ]

    all_articles = []

    for q in queries:
        xml = fetch_rss(q)
        items = parse_rss(xml)
        for item in items:
            item["label"] = q
            all_articles.append(item)
        time.sleep(1)

    data = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "articles": all_articles
    }

    OUTPUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
