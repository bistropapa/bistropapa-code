import json
import re
import html
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen
from urllib.parse import quote
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

OUTPUT_FILE = "news.json"
MAX_PER_FEED = 40
MAX_TOTAL = 100

FEEDS = {
    "料理・食": [
        "https://news.google.com/rss/search?q=" + quote("料理 OR 食品 OR レシピ OR 食育 OR 健康 food when:7d") + "&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=" + quote("外食 OR 中食 OR 冷凍食品 OR 食材 OR 栄養 when:7d") + "&hl=ja&gl=JP&ceid=JP:ja",
    ],
    "パパ・育児": [
        "https://news.google.com/rss/search?q=" + quote("育児 OR 子育て OR パパ OR 家族 OR 共食 OR 家庭教育 when:7d") + "&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=" + quote("親子 OR 保育 OR 教育 OR 子ども 食育 when:7d") + "&hl=ja&gl=JP&ceid=JP:ja",
    ],
    "AI・テクノロジー": [
        "https://news.google.com/rss/search?q=" + quote("AI OR 人工知能 OR 生成AI OR DX OR テクノロジー when:7d") + "&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=" + quote("AI 活用 OR 業務効率化 OR デジタル教育 OR edtech when:7d") + "&hl=ja&gl=JP&ceid=JP:ja",
    ],
}

NOISE_KEYWORDS = [
    "市役所", "市議会", "町役場", "町議会", "村役場", "村議会", "県庁", "県議会",
    "自治体", "広報", "議案", "入札", "告示", "公告", "選挙管理委員会",
    "消防本部", "水道局", "上下水道", "教育委員会", "農業委員会",
    "地域おこし協力隊", "ふるさと納税", "会計年度任用職員", "公民館",
]

BUSINESS_TAG_LABELS = {
    "health_management": "健康経営",
    "online_cooking": "オンライン料理教室",
    "ai_utilization": "AI活用",
    "food_education": "食育",
    "family_communication": "共食・家庭",
    "recipe_business": "レシピ活用",
    "corporate_training": "企業研修",
    "content_marketing": "発信ネタ",
    "product_development": "商品開発",
    "community": "コミュニティ",
}

def fetch_url(url):
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        },
    )
    with urlopen(req, timeout=20) as res:
        return res.read()

def strip_html(text):
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def extract_source_from_title(title):
    if " - " in title:
        parts = title.rsplit(" - ", 1)
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
    return title.strip(), "不明"

def detect_subcategory(text):
    t = text.lower()
    if any(k in t for k in ["新商品", "発売", "新発売", "販売開始"]):
        return "新商品"
    if any(k in t for k in ["調査", "アンケート", "分析", "レポート"]):
        return "調査"
    if any(k in t for k in ["レシピ", "料理", "献立", "食材"]):
        return "レシピ・料理"
    if any(k in t for k in ["健康", "栄養", "ウェルビーイング", "wellbeing", "well-being"]):
        return "健康"
    if any(k in t for k in ["ai", "人工知能", "生成ai", "dx", "デジタル"]):
        return "AI/DX"
    if any(k in t for k in ["育児", "子育て", "親子", "パパ", "家族"]):
        return "育児・家庭"
    if any(k in t for k in ["教育", "学習", "学校", "食育"]):
        return "教育"
    if any(k in t for k in ["企業", "法人", "研修", "導入", "事例"]):
        return "企業動向"
    return "話題"

def assign_business_tags(title, description, category, source, subcategory):
    text = f"{title} {description} {category} {source} {subcategory}".lower()
    tags = []

    def add(tag):
        if tag not in tags:
            tags.append(tag)

    # 事業タグ判定
    if any(k in text for k in [
        "健康経営", "ウェルビーイング", "wellbeing", "well-being",
        "従業員健康", "健康支援", "福利厚生", "メンタルヘルス", "栄養"
    ]):
        add("health_management")

    if any(k in text for k in [
        "オンライン", "動画講座", "ライブ配信", "zoom", "料理教室", "クッキングスクール", "講座"
    ]):
        add("online_cooking")

    if any(k in text for k in [
        "ai", "人工知能", "生成ai", "chatgpt", "llm", "dx", "自動化", "業務効率化"
    ]):
        add("ai_utilization")

    if any(k in text for k in [
        "食育", "教育", "学校給食", "学習", "子ども向け", "親子体験", "栄養教育"
    ]):
        add("food_education")

    if any(k in text for k in [
        "家族", "家庭", "共食", "団らん", "パパ", "子育て", "育児", "親子"
    ]):
        add("family_communication")

    if any(k in text for k in [
        "レシピ", "献立", "料理", "時短", "作り置き", "食材", "調理"
    ]):
        add("recipe_business")

    if any(k in text for k in [
        "企業研修", "研修", "社員教育", "人材育成", "セミナー", "法人向け", "導入事例"
    ]):
        add("corporate_training")

    if any(k in text for k in [
        "トレンド", "話題", "注目", "ランキング", "調査", "分析", "新商品", "新サービス"
    ]):
        add("content_marketing")

    if any(k in text for k in [
        "新商品", "商品開発", "共同開発", "発売", "開発", "リニューアル", "メニュー開発"
    ]):
        add("product_development")

    if any(k in text for k in [
        "コミュニティ", "イベント", "参加型", "交流", "会員", "ファン", "地域連携"
    ]):
        add("community")

    # カテゴリ別の最低保証タグ
    if not tags:
        if category == "AI・テクノロジー":
            add("ai_utilization")
            add("content_marketing")
        elif category == "料理・食":
            add("recipe_business")
            add("content_marketing")
        elif category == "パパ・育児":
            add("family_communication")
            add("food_education")
        else:
            add("content_marketing")

    return tags

def summarize(title, description, category, subcategory, business_tags):
    base = strip_html(description)
    title_clean = strip_html(title)

    if base:
        summary = base[:90]
    else:
        summary = title_clean

    if len(summary) < 25:
        summary = f"{subcategory}に関する話題。{category}分野で注目されるニュースです。"

    if business_tags:
        labels = [BUSINESS_TAG_LABELS[t] for t in business_tags if t in BUSINESS_TAG_LABELS]
        summary += f"／事業視点: {', '.join(labels[:3])}"

    return summary[:140]

def is_noise(title, description, source):
    text = f"{title} {description} {source}".lower()
    for kw in NOISE_KEYWORDS:
        if kw.lower() in text:
            return True
    return False

def parse_pubdate(pubdate):
    if not pubdate:
        return ""
    try:
        dt = parsedate_to_datetime(pubdate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return pubdate

def item_link(item):
    node = item.find("link")
    if node is not None and node.text:
        return node.text.strip()
    return ""

def item_text(item, tag_name):
    node = item.find(tag_name)
    if node is not None and node.text:
        return node.text.strip()
    return ""

def fetch_feed(feed_url, category):
    raw = fetch_url(feed_url)
    root = ET.fromstring(raw)
    items = root.findall(".//item")
    results = []

    for item in items[:MAX_PER_FEED]:
        raw_title = item_text(item, "title")
        description = strip_html(item_text(item, "description"))
        link = item_link(item)
        pubdate_raw = item_text(item, "pubDate")

        title, source = extract_source_from_title(raw_title)

        if is_noise(title, description, source):
            continue

        text_for_detect = f"{title} {description}"
        subcategory = detect_subcategory(text_for_detect)
        business_tags = assign_business_tags(title, description, category, source, subcategory)
        business_tag_labels = [BUSINESS_TAG_LABELS[t] for t in business_tags if t in BUSINESS_TAG_LABELS]
        summary = summarize(title, description, category, subcategory, business_tags)

        results.append({
            "title": title,
            "source": source,
            "category": category,
            "subcategory": subcategory,
            "summary": summary,
            "business_tags": business_tags,
            "business_tag_labels": business_tag_labels,
            "published": parse_pubdate(pubdate_raw),
            "link": link,
        })

    return results

def dedupe_articles(items):
    seen = set()
    deduped = []
    for item in items:
        key = (item["title"].strip().lower(), item["source"].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped

def main():
    all_articles = []

    for category, feed_urls in FEEDS.items():
        for feed_url in feed_urls:
            try:
                articles = fetch_feed(feed_url, category)
                all_articles.extend(articles)
            except Exception as e:
                print(f"Feed fetch failed: {feed_url} / {e}")

    all_articles = dedupe_articles(all_articles)
    all_articles.sort(key=lambda x: x.get("published", ""), reverse=True)

    data = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count": min(len(all_articles), MAX_TOTAL),
        "articles": all_articles[:MAX_TOTAL],
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Generated {OUTPUT_FILE} with {data['count']} articles.")

if __name__ == "__main__":
    main()
