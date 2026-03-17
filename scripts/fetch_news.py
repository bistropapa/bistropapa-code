#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

OUTPUT_FILE = Path("news.json")

MAX_PER_QUERY = 50
MAX_PER_CATEGORY = 100

BLOCKED_DOMAINS = [
    "lg.jp",
    "go.jp",
    "prtimes.jp",
    "city.",
    "pref.",
    "town.",
    "village.",
    "vill.",
    "townnews.co.jp"
]

CATEGORY_QUERIES = [
    {
        "label": "料理・食",
        "queries": [
            "料理 OR 食 OR レシピ OR 外食 OR 飲食店 OR 食品 OR グルメ OR フードテック -site:lg.jp -site:go.jp -site:prtimes.jp",
            "食品業界 OR 飲食業界 OR フード OR 食材 OR 健康食 OR 冷凍食品 OR 発酵 -site:lg.jp -site:go.jp -site:prtimes.jp",
            "食卓 OR 家庭料理 OR 献立 OR 料理研究 OR 飲食トレンド -site:lg.jp -site:go.jp -site:prtimes.jp"
        ]
    },
    {
        "label": "パパ・育児",
        "queries": [
            "育児 OR 子育て OR パパ OR 父親 OR 家事育児 OR 家族 -site:lg.jp -site:go.jp -site:prtimes.jp",
            "父親育児 OR 男性育休 OR 共働き OR 子ども OR 保育 OR 教育 -site:lg.jp -site:go.jp -site:prtimes.jp",
            "パパ 子育て OR 家族コミュニケーション OR 食育 -site:lg.jp -site:go.jp -site:prtimes.jp"
        ]
    },
    {
        "label": "AI・テクノロジー",
        "queries": [
            "AI OR 生成AI OR ChatGPT OR OpenAI OR Gemini OR Claude -site:prtimes.jp",
            "DX OR AI活用 OR AIエージェント OR 業務効率化 OR 自動化 -site:prtimes.jp",
            "テクノロジー OR SaaS OR ソフトウェア OR 機械学習 OR LLM -site:prtimes.jp"
        ]
    }
]


def fetch_rss(query: str) -> bytes:
    url = (
        "https://news.google.com/rss/search?"
        f"q={urllib.parse.quote(query)}&hl=ja&gl=JP&ceid=JP:ja"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.read()


def clean_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def parse_rss(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    items = []

    for item in root.findall(".//item")[:MAX_PER_QUERY]:
        title = clean_text(item.findtext("title", ""))
        link = clean_text(item.findtext("link", ""))
        source = clean_text(item.findtext("source", ""))
        pub_date = clean_text(item.findtext("pubDate", ""))

        published_at = ""
        if pub_date:
            try:
                dt = parsedate_to_datetime(pub_date)
                published_at = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                published_at = ""

        items.append({
            "title": title,
            "url": link,
            "source": source,
            "publishedAt": published_at,
        })

    return items


def is_blocked(item):
    source = (item.get("source") or "").lower()
    title = (item.get("title") or "").lower()
    url = (item.get("url") or "").lower()

    for domain in BLOCKED_DOMAINS:
        if domain in source or domain in url or domain in title:
            return True

    blocked_words = [
        "プレスリリース",
        "広報",
        "お知らせ",
        "開催します",
        "募集します",
        "ご案内"
    ]
    for w in blocked_words:
        if w.lower() in title:
            return True

    return False


def dedupe_key(item):
    title = item.get("title", "").lower()
    title = re.sub(r"\s+", "", title)
    return title


def main():
    all_articles = []

    for category in CATEGORY_QUERIES:
        label = category["label"]
        category_items = []
        seen = set()

        for query in category["queries"]:
            try:
                xml = fetch_rss(query)
                items = parse_rss(xml)
            except Exception:
                items = []

            for item in items:
                if is_blocked(item):
                    continue

                key = dedupe_key(item)
                if key in seen:
                    continue
                seen.add(key)

                item["label"] = label
                category_items.append(item)

                if len(category_items) >= MAX_PER_CATEGORY:
                    break

            if len(category_items) >= MAX_PER_CATEGORY:
                break

            time.sleep(1)

        all_articles.extend(category_items)

    data = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "articles": all_articles
    }

    OUTPUT_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


if __name__ == "__main__":
    main()
