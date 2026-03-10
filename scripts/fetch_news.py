import os
import json
import requests
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("NEWS_API_KEY", "")
JST = timezone(timedelta(hours=9))

QUERIES = [
    {"label": "料理・食", "q": "料理 OR 食卓 OR レシピ OR 食育", "lang": "jp"},
    {"label": "パパ・育児", "q": "パパ料理 OR 父親 育児 OR イクメン", "lang": "jp"},
    {"label": "AI・テクノロジー", "q": "AI 料理 OR フードテック OR 食品テクノロジー", "lang": "jp"},
]

results = []

for item in QUERIES:
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": item["q"],
        "language": "jp",
        "sortBy": "publishedAt",
        "pageSize": 5,
        "apiKey": API_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        articles = data.get("articles", [])
        for a in articles:
            title = a.get("title") or ""
            url_a = a.get("url") or ""
            source = (a.get("source") or {}).get("name") or ""
            published = a.get("publishedAt") or ""
            if title and url_a:
                results.append({
                    "label": item["label"],
                    "title": title,
                    "url": url_a,
                    "source": source,
                    "publishedAt": published,
                })
    except Exception as e:
        print(f"Error fetching {item['label']}: {e}")

output = {
    "updated": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
    "articles": results,
}

with open("news.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"Done. {len(results)} articles saved.")
