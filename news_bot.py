"""
News bot: pulls RSS feeds, translates to Turkish, dedupes, and sends to Telegram.
Run every 15 min via GitHub Actions.
"""
import os
import sys
import json
import time
import hashlib
import re
from pathlib import Path

import feedparser
import requests
from deep_translator import GoogleTranslator

# ---------- Config ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = Path("seen.json")
MAX_SEEN = 2000          # keep last N hashes (FIFO trim)
MAX_PER_FEED = 25        # how many items to scan per feed each run
SLEEP_BETWEEN_MSGS = 1.2 # seconds (Telegram + translate rate-limit safety)

# Feeds: (emoji, category, source_name, url, source_lang)
# Verify each URL once after deploy; replace any that 404.
FEEDS = [
    # 🇹🇷 Türkiye - genel/siyaset (piyasayı dolaylı etkiler)
    ("🇹🇷", "Türkiye", "BBC Türkçe",    "https://feeds.bbci.co.uk/turkce/rss.xml",       "tr"),
    ("🇹🇷", "Türkiye", "DW Türkçe",     "https://rss.dw.com/xml/rss-tur-all",            "tr"),
    ("🇹🇷", "Türkiye", "Sözcü",         "https://www.sozcu.com.tr/feed/",                "tr"),
    ("🇹🇷", "Türkiye", "Hürriyet",      "https://www.hurriyet.com.tr/rss/gundem",        "tr"),

    # 💰 BIST / Türkiye Ekonomi
    ("💰", "BIST",      "Bloomberg HT",  "https://www.bloomberght.com/rss",                "tr"),
    ("💰", "BIST",      "Dünya Gzt.",    "https://www.dunya.com/rss?dunya",                "tr"),
    ("💰", "BIST",      "Hürriyet Eko",  "https://www.hurriyet.com.tr/rss/ekonomi",        "tr"),
    ("💰", "BIST",      "Sözcü Eko",     "https://www.sozcu.com.tr/category/ekonomi/feed/","tr"),

    # 📈 ABD / Global Piyasalar
    ("📈", "Piyasalar", "CNBC",          "https://www.cnbc.com/id/100003114/device/rss/rss.html",      "en"),
    ("📈", "Piyasalar", "CNBC Markets",  "https://www.cnbc.com/id/15839069/device/rss/rss.html",       "en"),
    ("📈", "Piyasalar", "Yahoo Finance", "https://finance.yahoo.com/news/rssindex",                    "en"),
    ("📈", "Piyasalar", "MarketWatch",   "https://feeds.content.dowjones.io/public/rss/mw_topstories", "en"),
    ("📈", "Piyasalar", "WSJ Markets",   "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",              "en"),

    # 🏦 Makro / Merkez Bankaları (faiz, enflasyon)
    ("🏦", "Makro",     "Fed",           "https://www.federalreserve.gov/feeds/press_all.xml",  "en"),

    # 🌍 Dünya (jeopolitik - petrol, dolar, emtia tetikleyici)
    ("🌍", "Dünya",     "BBC World",     "http://feeds.bbci.co.uk/news/world/rss.xml",    "en"),
    ("🌍", "Dünya",     "Al Jazeera",    "https://www.aljazeera.com/xml/rss/all.xml",     "en"),

    # 💻 Teknoloji (NASDAQ/teknoloji hisseleri)
    ("💻", "Teknoloji", "Webrazzi",      "https://webrazzi.com/feed/",                    "tr"),
    ("💻", "Teknoloji", "TechCrunch",    "https://techcrunch.com/feed/",                  "en"),
    ("💻", "Teknoloji", "The Verge",     "https://www.theverge.com/rss/index.xml",        "en"),
]


# ---------- Helpers ----------
def normalize_title(title: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — for fuzzy dedup."""
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
    state["hashes"] = state["hashes"][-MAX_SEEN:]
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
        return text  # fallback to original


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


def format_msg(emoji, category, source, title_tr, link, original_title, lang):
    title_tr = html_escape(title_tr.strip())
    source = html_escape(source)
    parts = [
        f"{emoji} <b>{html_escape(category)}</b> · <i>{source}</i>",
        "",
        title_tr,
    ]
    # Include original title if we translated, so user can spot mistranslations
    if lang != "tr" and original_title.strip().lower() != title_tr.strip().lower():
        parts.append(f"<i>🔤 {html_escape(original_title.strip())}</i>")
    parts += ["", f'<a href="{html_escape(link)}">Habere git →</a>']
    return "\n".join(parts)


# ---------- Per-feed worker ----------
def process_feed(emoji, category, source, url, lang, state, first_run):
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0 NewsBot"})
    except Exception as e:
        print(f"[{source}] parse error: {e}", file=sys.stderr)
        return 0

    if not feed.entries:
        print(f"[{source}] empty/broken feed")
        return 0

    seen_h = set(state["hashes"])
    seen_t = set(state["title_hashes"])

    new_items = []
    for entry in feed.entries[:MAX_PER_FEED]:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title:
            continue
        h = hash_str(normalize_title(title) + "|" + link)
        th = hash_str(normalize_title(title))
        if h in seen_h or th in seen_t:
            continue
        new_items.append((h, th, title, link))

    if first_run:
        # Mark everything as seen, send nothing (avoid 100+ message dump on setup)
        for h, th, _, _ in new_items:
            state["hashes"].append(h)
            state["title_hashes"].append(th)
        print(f"[{source}] first run: marked {len(new_items)} as seen")
        return 0

    sent = 0
    # Oldest-first so Telegram chronology feels right
    for h, th, title, link in reversed(new_items):
        title_tr = translate_to_tr(title, lang)
        msg = format_msg(emoji, category, source, title_tr, link, title, lang)
        if send_telegram(msg):
            state["hashes"].append(h)
            state["title_hashes"].append(th)
            sent += 1
            time.sleep(SLEEP_BETWEEN_MSGS)
    if sent:
        print(f"[{source}] sent {sent}")
    return sent


# ---------- Main ----------
def main():
    state = load_state()
    first_run = not state["hashes"] and not state["title_hashes"]
    if first_run:
        print("⚠ First run detected — marking current items as seen without sending.")
        print("  Next run (~15 min) will send only new items.\n")

    total = 0
    for emoji, category, source, url, lang in FEEDS:
        total += process_feed(emoji, category, source, url, lang, state, first_run)

    save_state(state)
    print(f"\nDone. Total sent: {total}. Tracked: {len(state['hashes'])} items.")


if __name__ == "__main__":
    main()
