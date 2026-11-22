"""
Küçük Kanal Radarı (2. bot)
- 4 saatte bir çalışır
- Amerika'da ilk 150, Türkiye'de ilk 100 videoya bakar (ücretsiz kotanın elverdiği en derin arama)
- 5000 abone altı kanalların son 6 saatte 10 bin+ izlenen Shorts'larını ayrı Telegram botuna gönderir
- Hindistan hariç, aynı videoyu tekrar göndermez (sent_small.json)
"""
from datetime import datetime, timedelta, timezone

import trend_bot as tb

# ===== AYARLAR =====
DEEP_REGIONS = [("US", "en", 3), ("TR", "tr", 2)]   # (ülke, dil, kaç sayfa x 50 video)
SMALL_MAX_SUBS = 5000
SMALL_MAX_AGE_HOURS = 6
SMALL_MIN_VIEWS = 10_000
SENT_SMALL = "sent_small.json"


def deep_search():
    since = (datetime.now(timezone.utc) - timedelta(hours=SMALL_MAX_AGE_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = set()
    for region, lang, pages in DEEP_REGIONS:
        token = None
        for _ in range(pages):
            params = {
                "part": "id", "type": "video", "videoDuration": "short",
                "order": "viewCount", "publishedAfter": since, "maxResults": 50,
                "regionCode": region, "relevanceLanguage": lang,
            }
            if token:
                params["pageToken"] = token
            data = tb.get("https://www.googleapis.com/youtube/v3/search", params)
            for it in data.get("items", []):
                ids.add(it["id"]["videoId"])
            token = data.get("nextPageToken")
            if not token:
                break
    return list(ids)


if __name__ == "__main__":
    if not tb.TG_TOKEN_SMALL:
        raise SystemExit("TG_TOKEN_SMALL yok")
    videos = tb.get_details(deep_search())
    countries = tb.channel_countries(videos) if videos else {}
    sent = tb.load_sent(SENT_SMALL)
    rows = [r for r in tb.score(videos, countries, SMALL_MAX_AGE_HOURS, SMALL_MIN_VIEWS, SMALL_MAX_SUBS)
            if r["id"] not in sent][:tb.TOP_N]
    if rows:
        tb.send_telegram(rows, "🌱 Küçük Kanal Radarı", tb.TG_TOKEN_SMALL)
    now_ts = datetime.now(timezone.utc).timestamp()
    for r in rows:
        sent[r["id"]] = now_ts
    tb.save_sent(sent, SENT_SMALL)
    print(f"{len(videos)} video incelendi, {len(rows)} yeni küçük kanal videosu gönderildi.")
