"""
Saatlik Global Trend Shorts Botu
- Amerika, Avrupa ve Türkiye ağırlıklı; Hindistan içerikleri tamamen dışarıda
- Son 4 saatte yüklenmiş, en az 40 bin izlenmeli Shorts'ları bulur (YouTube Data API, ücretsiz)
- "Saatte izlenme" hızına göre sıralar, en fazla 50 tanesini Telegram'a gönderir
- Daha önce gönderdiği videoyu tekrar göndermez (sent.json)
- 2. bot (Küçük Kanal Radarı) ayrı dosyada: small_radar.py (6 saatte bir, derin arama)
Ek paket gerekmez (sadece Python).
"""
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_KEY = os.environ["YT_API_KEY"]
TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]

# ===== AYARLAR =====
# Her saat: Amerika + Türkiye; 2 saatte bir de sırayla bir Avrupa ülkesi
FIXED_REGIONS = [("US", "en"), ("TR", "tr")]
EUROPE_ROTATION = [("GB", "en"), ("DE", "de"), ("FR", "fr"), ("ES", "es"), ("IT", "it")]
TOP_N = 50
PER_MESSAGE = 10         # Telegram mesajı çok uzamasın diye 10'arlı gönder
MAX_SECONDS = 180        # Shorts sınırı: 3 dakika
MAX_AGE_HOURS = 4        # 4 saatten eski videoları gösterme
MIN_VIEWS = 40_000       # en az 40 bin izlenme
SEND_IF_EMPTY = False    # uygun video yoksa mesaj atma

# Küçük Kanal Radarı (2. bot)
TG_TOKEN_SMALL = os.environ.get("TG_TOKEN_SMALL", "")
SEARCH_HOURS = MAX_AGE_HOURS

# Hindistan filtresi
BLOCKED_COUNTRIES = {"IN"}
BLOCKED_LANGS = ("hi", "ta", "te", "bn", "mr", "gu", "kn", "ml", "pa", "or", "ur", "as", "bho")
INDIC_SCRIPT = re.compile(r"[ऀ-෿]")  # Devanagari, Bengalce, Tamilce vb. harfler


def get(url, params):
    params["key"] = API_KEY
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(full, timeout=30) as r:
        return json.loads(r.read().decode())


def iso_to_seconds(d):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 99999
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def regions_this_hour():
    # Avrupa 2 saatte bir (kota Küçük Kanal Radarı'na da yetsin diye)
    hour = datetime.now(timezone.utc).hour
    if hour % 2:
        return FIXED_REGIONS
    return FIXED_REGIONS + [EUROPE_ROTATION[(hour // 2) % len(EUROPE_ROTATION)]]


def find_ids():
    since = (datetime.now(timezone.utc) - timedelta(hours=SEARCH_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = set()
    for region, lang in regions_this_hour():
        data = get("https://www.googleapis.com/youtube/v3/search", {
            "part": "id",
            "type": "video",
            "videoDuration": "short",
            "order": "viewCount",
            "publishedAfter": since,
            "maxResults": 50,
            "regionCode": region,
            "relevanceLanguage": lang,
        })
        for it in data.get("items", []):
            ids.add(it["id"]["videoId"])
    return list(ids)


def get_details(ids):
    out = []
    for i in range(0, len(ids), 50):
        data = get(
            "https://www.googleapis.com/youtube/v3/videos",
            {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i + 50])},
        )
        out.extend(data.get("items", []))
    return out


CHANNEL_SUBS = {}  # kanal -> abone sayısı (gizliyse None)


def channel_countries(videos):
    ch_ids = list({v["snippet"]["channelId"] for v in videos})
    countries = {}
    for i in range(0, len(ch_ids), 50):
        data = get(
            "https://www.googleapis.com/youtube/v3/channels",
            {"part": "snippet,statistics", "id": ",".join(ch_ids[i:i + 50])},
        )
        for c in data.get("items", []):
            countries[c["id"]] = c["snippet"].get("country", "")
            st = c.get("statistics", {})
            CHANNEL_SUBS[c["id"]] = None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0))
    return countries


def is_indian(v, countries):
    sn = v["snippet"]
    if countries.get(sn["channelId"], "") in BLOCKED_COUNTRIES:
        return True
    for key in ("defaultAudioLanguage", "defaultLanguage"):
        if (sn.get(key) or "").lower().split("-")[0] in BLOCKED_LANGS:
            return True
    if INDIC_SCRIPT.search(sn.get("title", "") + " " + sn.get("channelTitle", "")):
        return True
    return False


def score(videos, countries, max_age=None, min_views=None, max_subs=None):
    max_age = MAX_AGE_HOURS if max_age is None else max_age
    min_views = MIN_VIEWS if min_views is None else min_views
    now = datetime.now(timezone.utc)
    rows = []
    for v in videos:
        if is_indian(v, countries):
            continue
        secs = iso_to_seconds(v["contentDetails"].get("duration"))
        if secs > MAX_SECONDS:
            continue
        views = int(v["statistics"].get("viewCount", 0))
        pub = datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
        age_h = (now - pub).total_seconds() / 3600
        if age_h > max_age or views < min_views:
            continue
        subs = CHANNEL_SUBS.get(v["snippet"]["channelId"])
        if max_subs is not None and (subs is None or subs >= max_subs):
            continue
        hours = max(age_h, 0.1)
        rows.append({
            "title": v["snippet"]["title"],
            "channel": v["snippet"]["channelTitle"],
            "country": countries.get(v["snippet"]["channelId"], ""),
            "views": views,
            "subs": subs,
            "per_hour": int(views / hours),
            "id": v["id"],
            "url": f"https://youtube.com/shorts/{v['id']}",
        })
    rows.sort(key=lambda r: r["per_hour"], reverse=True)
    return rows


# ===== Daha önce gönderilenleri hatırlama =====
SENT_FILE = "sent.json"
KEEP_HOURS = 48  # 48 saatten eski kayıtları sil (dosya şişmesin)


def load_sent(path=SENT_FILE):
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    limit = datetime.now(timezone.utc).timestamp() - KEEP_HOURS * 3600
    return {vid: t for vid, t in data.items() if t > limit}


def save_sent(sent, path=SENT_FILE):
    with open(path, "w") as f:
        json.dump(sent, f)


def tg_send(text, token=None):
    body = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token or TG_TOKEN}/sendMessage", body, timeout=30)


def send_telegram(rows, title="🔥 Global Trend Shorts", token=None):
    saat = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m %H:%M")
    if not rows:
        tg_send(f"{title} — {saat}\n\nBu saat sonuç bulunamadı.", token)
        return
    total = (len(rows) + PER_MESSAGE - 1) // PER_MESSAGE
    for part in range(total):
        chunk = rows[part * PER_MESSAGE:(part + 1) * PER_MESSAGE]
        head = f"{title} — {saat}"
        if total > 1:
            head += f" ({part + 1}/{total})"
        lines = [head + "\n"]
        for i, r in enumerate(chunk, part * PER_MESSAGE + 1):
            flag = f" [{r['country']}]" if r["country"] else ""
            abone = f" ({r['subs']:,} abone)" if r.get("subs") is not None else ""
            lines.append(
                f"{i}. {r['title'][:80]}\n"
                f"   👤 {r['channel']}{flag}{abone} | 👁 {r['views']:,} | ⚡ {r['per_hour']:,}/saat\n"
                f"   {r['url']}\n"
            )
        tg_send("\n".join(lines), token)


if __name__ == "__main__":
    sent = load_sent()
    videos = get_details(find_ids())
    countries = channel_countries(videos) if videos else {}
    rows = [r for r in score(videos, countries) if r["id"] not in sent][:TOP_N]
    if rows or SEND_IF_EMPTY:
        send_telegram(rows)
    now_ts = datetime.now(timezone.utc).timestamp()
    for r in rows:
        sent[r["id"]] = now_ts
    save_sent(sent)
    print(f"{len(rows)} yeni video gönderildi.")

