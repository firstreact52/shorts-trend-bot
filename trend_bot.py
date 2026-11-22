"""
Saatlik Global Trend Shorts Botu
- Son 4 saatte yüklenmiş, en az 200 bin izlenmeli Shorts'ları bulur (YouTube Data API, ücretsiz)
- "Saatte izlenme" hızına göre sıralar, ilk 10'u Telegram'a gönderir
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
# Bölgeler: "" = genel/global. Kota için az tutuldu (her arama 100 birim).
REGIONS = ["", "US", "IN"]
TOP_N = 10
MAX_SECONDS = 180        # Shorts sınırı: 3 dakika
MAX_AGE_HOURS = 4        # 4 saatten eski videoları gösterme
MIN_VIEWS = 200_000      # en az 200 bin izlenme
SEND_IF_EMPTY = False    # uygun video yoksa mesaj atma


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


def find_ids():
    since = (datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = set()
    for region in REGIONS:
        p = {
            "part": "id",
            "type": "video",
            "videoDuration": "short",
            "order": "viewCount",
            "publishedAfter": since,
            "maxResults": 50,
        }
        if region:
            p["regionCode"] = region
        data = get("https://www.googleapis.com/youtube/v3/search", p)
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


def score(videos):
    now = datetime.now(timezone.utc)
    rows = []
    for v in videos:
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


def send_telegram(rows):
    saat = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m %H:%M")
    lines = [f"🔥 Global Trend Shorts — {saat}\n"]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i}. {r['title'][:80]}\n"
            f"   👤 {r['channel']} | 👁 {r['views']:,} | ⚡ {r['per_hour']:,}/saat\n"
            f"   {r['url']}\n"
        )
    if not rows:
        lines.append("Bu saat sonuç bulunamadı.")
    body = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": "\n".join(lines),
        "disable_web_page_preview": "true",
    }).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", body, timeout=30)


if __name__ == "__main__":
    sent = load_sent()
    rows = [r for r in score(get_details(find_ids())) if r["id"] not in sent][:TOP_N]
    if rows or SEND_IF_EMPTY:
        send_telegram(rows)
    now_ts = datetime.now(timezone.utc).timestamp()
    for r in rows:
        sent[r["id"]] = now_ts
    save_sent(sent)
    print(f"{len(rows)} yeni video gönderildi.")
