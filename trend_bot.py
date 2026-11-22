"""
Saatlik Global Trend Shorts Botu
- Amerika, Avrupa ve Türkiye ağırlıklı; Hindistan içerikleri tamamen dışarıda
- Son 4 saatte yüklenmiş, en az 200 bin izlenmeli Shorts'ları bulur (YouTube Data API, ücretsiz)
- "Saatte izlenme" hızına göre sıralar, en fazla 50 tanesini Telegram'a gönderir
- Daha önce gönderdiği videoyu tekrar göndermez (sent.json)
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
# Her saat: Amerika + Türkiye + sırayla bir Avrupa ülkesi (ücretsiz kotayı aşmamak için 3 arama)
FIXED_REGIONS = [("US", "en"), ("TR", "tr")]
EUROPE_ROTATION = [("GB", "en"), ("DE", "de"), ("FR", "fr"), ("ES", "es"), ("IT", "it")]
TOP_N = 50
PER_MESSAGE = 10         # Telegram mesajı çok uzamasın diye 10'arlı gönder
MAX_SECONDS = 180        # Shorts sınırı: 3 dakika
MAX_AGE_HOURS = 4        # 4 saatten eski videoları gösterme
MIN_VIEWS = 200_000      # en az 200 bin izlenme
SEND_IF_EMPTY = False    # uygun video yoksa mesaj atma

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
    hour = datetime.now(timezone.utc).hour
    return FIXED_REGIONS + [EUROPE_ROTATION[hour % len(EUROPE_ROTATION)]]


def find_ids():
    since = (datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
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


def channel_countries(videos):
    ch_ids = list({v["snippet"]["channelId"] for v in videos})
    countries = {}
    for i in range(0, len(ch_ids), 50):
        data = get(
            "https://www.googleapis.com/youtube/v3/channels",
            {"part": "snippet", "id": ",".join(ch_ids[i:i + 50])},
        )
        for c in data.get("items", []):
            countries[c["id"]] = c["snippet"].get("country", "")
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


def score(videos, countries):
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
        if age_h > MAX_AGE_HOURS or views < MIN_VIEWS:
            continue
        hours = max(age_h, 0.1)
        rows.append({
            "title": v["snippet"]["title"],
            "channel": v["snippet"]["channelTitle"],
            "country": countries.get(v["snippet"]["channelId"], ""),
            "views": views,
            "per_hour": int(views / hours),
            "id": v["id"],
            "url": f"https://youtube.com/shorts/{v['id']}",
        })
    rows.sort(key=lambda r: r["per_hour"], reverse=True)
    return rows


# ===== Daha önce gönderilenleri hatırlama =====
SENT_FILE = "sent.json"
KEEP_HOURS = 48  # 48 saatten eski kayıtları sil (dosya şişmesin)


def load_sent():
    try:
        with open(SENT_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    limit = datetime.now(timezone.utc).timestamp() - KEEP_HOURS * 3600
    return {vid: t for vid, t in data.items() if t > limit}


def save_sent(sent):
    with open(SENT_FILE, "w") as f:
        json.dump(sent, f)


def tg_send(text):
    body = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", body, timeout=30)


def send_telegram(rows):
    saat = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m %H:%M")
    if not rows:
        tg_send(f"🔥 Global Trend Shorts — {saat}\n\nBu saat sonuç bulunamadı.")
        return
    total = (len(rows) + PER_MESSAGE - 1) // PER_MESSAGE
    for part in range(total):
        chunk = rows[part * PER_MESSAGE:(part + 1) * PER_MESSAGE]
        head = f"🔥 Global Trend Shorts — {saat}"
        if total > 1:
            head += f" ({part + 1}/{total})"
        lines = [head + "\n"]
        for i, r in enumerate(chunk, part * PER_MESSAGE + 1):
            flag = f" [{r['country']}]" if r["country"] else ""
            lines.append(
                f"{i}. {r['title'][:80]}\n"
                f"   👤 {r['channel']}{flag} | 👁 {r['views']:,} | ⚡ {r['per_hour']:,}/saat\n"
                f"   {r['url']}\n"
            )
        tg_send("\n".join(lines))


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
