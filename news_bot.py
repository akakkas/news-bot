"""
News bot: pulls RSS feeds (finans + teknoloji),
çevirir, tekilleştirir, Gemini ile hisse analizi ekler,
Telegram'a gönderir. Her 15 dakikada GitHub Actions ile çalışır.
"""
import os
import sys
import json
import time
import hashlib
import re
import warnings
warnings.filterwarnings("ignore")

from pathlib import Path

import feedparser
import requests
from deep_translator import GoogleTranslator
import google.generativeai as genai

# ---------- Config ----------
TELEGRAM_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY   = os.environ.get("GEMINI_API_KEY", "")

STATE_FILE    = Path("seen.json")
MAX_SEEN      = 2000
MAX_PER_FEED  = 25
SLEEP_BETWEEN = 1.2

# ---------- Feeds (finans + teknoloji) ----------
FEEDS = [
    # 💰 BIST / Türkiye Ekonomi
    ("💰", "BIST",      "Bloomberg HT",  "https://www.bloomberght.com/rss",                 "tr"),
    ("💰", "BIST",      "Dünya Gzt.",    "https://www.dunya.com/rss?dunya",                 "tr"),
    ("💰", "BIST",      "Hürriyet Eko",  "https://www.hurriyet.com.tr/rss/ekonomi",         "tr"),
    ("💰", "BIST",      "Sözcü Eko",     "https://www.sozcu.com.tr/category/ekonomi/feed/", "tr"),
    # 📈 ABD / Global Piyasalar
    ("📈", "Piyasalar", "CNBC",          "https://www.cnbc.com/id/100003114/device/rss/rss.html",      "en"),
    ("📈", "Piyasalar", "CNBC Markets",  "https://www.cnbc.com/id/15839069/device/rss/rss.html",       "en"),
    ("📈", "Piyasalar", "Yahoo Finance", "https://finance.yahoo.com/news/rssindex",                    "en"),
    ("📈", "Piyasalar", "MarketWatch",   "https://feeds.content.dowjones.io/public/rss/mw_topstories", "en"),
    ("📈", "Piyasalar", "WSJ Markets",   "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",              "en"),
    # 🏦 Makro / Merkez Bankaları
    ("🏦", "Makro",     "Fed",           "https://www.federalreserve.gov/feeds/press_all.xml", "en"),
    # 💻 Teknoloji
    ("💻", "Teknoloji", "Webrazzi",      "https://webrazzi.com/feed/",                       "tr"),
    ("💻", "Teknoloji", "TechCrunch",    "https://techcrunch.com/feed/",                     "en"),
    ("💻", "Teknoloji", "The Verge",     "https://www.theverge.com/rss/index.xml",           "en"),
]


# ---------- Helpers ----------
def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"[^\w\sğüşıöçĞÜŞİÖÇ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def hash_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"hashes": [], "title_hashes": []}

def save_state(state: dict) -> None:
    state["hashes"]       = state["hashes"][-MAX_SEEN:]
    state["title_hashes"] = state["title_hashes"][-MAX_SEEN:]
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )

def translate_to_tr(text: str, source_lang: str) -> str:
    if source_lang == "tr" or not text.strip():
        return text
    try:
        return GoogleTranslator(source="auto", target="tr").translate(text)
    except Exception as e:
        print(f"  translate failed: {e}", file=sys.stderr)
        return text

def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 429:
                retry = r.json().get("parameters", {}).get("retry_after", 5)
                print(f"  rate limited, sleeping {retry}s")
                time.sleep(retry + 1)
                continue
            r.raise_for_status()
            return True
        except Exception as e:
            print(f"  telegram send failed (try {attempt+1}): {e}", file=sys.stderr)
            time.sleep(2)
    return False

def format_msg(emoji, category, source, title_tr, link, original_title, lang, stocks=None):
    title_tr = html_escape(title_tr.strip())
    source   = html_escape(source)
    parts    = [
        f"{emoji} <b>{html_escape(category)}</b> · <i>{source}</i>",
        "",
        title_tr,
    ]
    if lang != "tr" and original_title.strip().lower() != title_tr.strip().lower():
        parts.append(f"<i>🔤 {html_escape(original_title.strip())}</i>")
    if stocks:
        parts.append(f"📌 <b>Etkilenebilir:</b> {', '.join(stocks)}")
    parts += ["", f'<a href="{html_escape(link)}">Habere git →</a>']
    return "\n".join(parts)


# ---------- Gemini Hisse Analizi ----------
def get_stock_impacts(items: list) -> dict:
    """
    Tüm yeni haberleri tek Gemini çağrısıyla analiz eder.
    Döndürür: {"0": ["GARAN", "YKBNK"], "1": ["AAPL"], ...}
    """
    if not GEMINI_API_KEY or not items:
        return {}

    headlines = "\n".join(f"{i}: {item['title']}" for i, item in enumerate(items))

    prompt = f"""Aşağıdaki haber başlıklarının her biri için BIST (Borsa İstanbul) veya ABD \
borsasında doğrudan etkilenebilecek hisse senetlerini belirt.

Kurallar:
- Sadece JSON döndür, başka hiçbir şey yazma.
- Format: {{"0": ["TICKER1", "TICKER2"], "1": [], "2": ["TICKER3"]}}
- Eğer ilgili hisse yoksa boş liste döndür.
- Maksimum 3 ticker per haber.
- BIST hisseleri: Türkçe kodlar (GARAN, YKBNK, THYAO, ASELS, KCHOL vb.)
- ABD hisseleri: standart kodlar (AAPL, NVDA, JPM vb.)
- Genel makro haberler (Fed, faiz, enflasyon) için bankacılık sektörü önder hisseleri öner.
- Teknoloji haberleri için ilgili tech hisseleri öner.

Haberler:
{headlines}"""

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model    = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        text     = response.text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"Gemini analizi başarısız: {e}", file=sys.stderr)
        return {}


# ---------- Feed işleme ----------
def collect_new_items(emoji, category, source, url, lang, state, first_run) -> list:
    """Yeni haberleri toplar ama göndermez. first_run'da sadece işaretler."""
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0 NewsBot"})
    except Exception as e:
        print(f"[{source}] parse error: {e}", file=sys.stderr)
        return []

    if not feed.entries:
        print(f"[{source}] empty/broken feed")
        return []

    seen_h = set(state["hashes"])
    seen_t = set(state["title_hashes"])

    new_items = []
    for entry in feed.entries[:MAX_PER_FEED]:
        title = (entry.get("title") or "").strip()
        link  = (entry.get("link")  or "").strip()
        if not title:
            continue
        h  = hash_str(normalize_title(title) + "|" + link)
        th = hash_str(normalize_title(title))
        if h in seen_h or th in seen_t:
            continue
        new_items.append({
            "h": h, "th": th,
            "title": title, "link": link,
            "emoji": emoji, "category": category,
            "source": source, "lang": lang,
        })

    if first_run:
        for item in new_items:
            state["hashes"].append(item["h"])
            state["title_hashes"].append(item["th"])
        print(f"[{source}] first run: marked {len(new_items)} as seen")
        return []

    print(f"[{source}] {len(new_items)} yeni haber")
    return list(reversed(new_items))  # eskiden yeniye


def send_all_items(all_items: list, stock_impacts: dict, state: dict) -> int:
    sent = 0
    for i, item in enumerate(all_items):
        stocks   = stock_impacts.get(str(i), []) or []
        title_tr = translate_to_tr(item["title"], item["lang"])
        msg      = format_msg(
            item["emoji"], item["category"], item["source"],
            title_tr, item["link"], item["title"], item["lang"],
            stocks=stocks if stocks else None,
        )
        if send_telegram(msg):
            state["hashes"].append(item["h"])
            state["title_hashes"].append(item["th"])
            sent += 1
            time.sleep(SLEEP_BETWEEN)
    return sent


# ---------- Main ----------
def main():
    state     = load_state()
    first_run = not state["hashes"] and not state["title_hashes"]
    if first_run:
        print("⚠ First run — marking current items as seen without sending.")
        print("  Next run (~15 min) will send only new items.\n")

    # 1. Tüm feedlerden yeni haberleri topla
    all_items = []
    for emoji, category, source, url, lang in FEEDS:
        items = collect_new_items(emoji, category, source, url, lang, state, first_run)
        all_items.extend(items)

    print(f"\nToplam yeni haber: {len(all_items)}")

    if not all_items:
        save_state(state)
        print("Yeni haber yok.")
        return

    # 2. Tek Gemini çağrısıyla tüm haberler için hisse analizi
    print("Gemini hisse analizi yapılıyor...")
    stock_impacts = get_stock_impacts(all_items)
    print(f"Analiz tamamlandı: {len(stock_impacts)} haber etiketlendi.")

    # 3. Telegram'a gönder
    sent = send_all_items(all_items, stock_impacts, state)
    save_state(state)
    print(f"\nDone. Gönderilen: {sent}. Takip edilen: {len(state['hashes'])} item.")


if __name__ == "__main__":
    main()
