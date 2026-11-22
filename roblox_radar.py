"""
Roblox Radarı (3. bot) - günde 1 kez
1) Roblox'ta şu an en çok oynanan 10 oyun + hızla yükselen 5 yeni oyun
2) viriri (@deepfriedsalte) ve benzer kanalların son 48 saatteki yeni Shorts'ları
3) Benzer animasyon kanallarında tutan videolar; tutan kanallar takip listesine kendiliğinden eklenir
Şarkı adı çıkarılmaz: videoyu açıp alttaki ses adına dokun -> "Bu sesi kullan".
Aynı videoyu tekrar göndermez (sent_roblox.json). Ek paket gerekmez.
"""
import json
import urllib.request
from datetime import datetime, timedelta, timezone

import trend_bot as tb

# ===== AYARLAR =====
TOP_GAMES = 10
RISING_GAMES = 5
START_HANDLES = ["@deepfriedsalte"]          # başlangıç kanalları (viriri)
MAX_WATCH = 30                               # en fazla kaç kanal takip edilsin
DISCOVER_QUERIES = ["dandys world animation", "roblox tweening animation"]
DISCOVER_HOURS = 72
DISCOVER_MIN_VIEWS = 50_000                  # benzer kanal videosu en az bu kadar izlenmeli
ADD_CHANNEL_VIEWS = 100_000                  # bu kadar izlenen videosu olan kanal listeye eklenir
NEW_VIDEO_HOURS = 48
WATCH_FILE = "watch_channels.json"
SENT_ROBLOX = "sent_roblox.json"
HOW_TO = "👉 Videoyu aç, alttaki ses adına dokun, \"Bu sesi kullan\" de."

# ===== Roblox oyun listesi =====
def roblox_message():
    req = urllib.request.Request(
        "https://apis.roblox.com/explore-api/v1/get-sorts?sessionId=trendbot&device=computer&country=all",
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    sorts = {s.get("sortId"): [g for g in (s.get("games") or []) if not g.get("isSponsored")]
             for s in data.get("sorts", [])}
    top = sorts.get("top-playing-now", [])[:TOP_GAMES]
    lines = ["🎮 Roblox'ta şu an en çok oynananlar\n"]
    for i, g in enumerate(top, 1):
        lines.append(f"{i}. {g['name']} — 👥 {g.get('playerCount', 0):,}")
    top_ids = {g["universeId"] for g in top}
    rising = [g for g in sorts.get("up-and-coming", []) if g["universeId"] not in top_ids][:RISING_GAMES]
    if rising:
        lines.append("\n🚀 Hızla yükselen yeni oyunlar\n")
        for i, g in enumerate(rising, 1):
            lines.append(f"{i}. {g['name']} — 👥 {g.get('playerCount', 0):,}")
    return "\n".join(lines)

# ===== Takip listesi =====
def load_watch():
    try:
        with open(WATCH_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_watch(watch):
    with open(WATCH_FILE, "w") as f:
        json.dump(watch, f, ensure_ascii=False, indent=1)

def add_start_handles(watch):
    known = {v.get("handle") for v in watch.values()}
    for h in START_HANDLES:
        if h in known:
            continue
        data = tb.get("https://www.googleapis.com/youtube/v3/channels", {"part": "snippet", "forHandle": h})
        for c in data.get("items", []):
            watch[c["id"]] = {"name": c["snippet"]["title"], "handle": h}

def watched_new_ids(watch):
    since = datetime.now(timezone.utc) - timedelta(hours=NEW_VIDEO_HOURS)
    ids = []
    for ch_id in watch:
        try:
            data = tb.get("https://www.googleapis.com/youtube/v3/playlistItems", {
                "part": "contentDetails", "playlistId": "UU" + ch_id[2:], "maxResults": 10,
            })
        except Exception as e:
            print("Kanal okunamadı:", ch_id, e)
            continue
        for it in data.get("items", []):
            cd = it["contentDetails"]
            pub = datetime.fromisoformat(cd.get("videoPublishedAt", "1970-01-01T00:00:00Z").replace("Z", "+00:00"))
            if pub >= since:
                ids.append(cd["videoId"])
    return ids

def discover_ids():
    since = (datetime.now(timezone.utc) - timedelta(hours=DISCOVER_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = set()
    for q in DISCOVER_QUERIES:
        data = tb.get("https://www.googleapis.com/youtube/v3/search", {
            "part": "id", "type": "video", "videoDuration": "short",
            "order": "viewCount", "publishedAfter": since, "maxResults": 50,
            "q": q, "regionCode": "US", "relevanceLanguage": "en",
        })
        for it in data.get("items", []):
            ids.add(it["id"]["videoId"])
    return list(ids)

def rows_from(videos, max_age, min_views):
    countries = tb.channel_countries(videos) if videos else {}
    return tb.score(videos, countries, max_age, min_views)

if __name__ == "__main__":
    saat = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m %H:%M")
    try:
        tb.tg_send(f"{roblox_message()}\n\n— {saat}")
    except Exception as e:
        print("Roblox listesi alınamadı:", e)

    sent = tb.load_sent(SENT_ROBLOX)
    watch = load_watch()
    add_start_handles(watch)

    # 1) Takip edilen kanalların yeni videoları
    watched = [r for r in rows_from(tb.get_details(watched_new_ids(watch)), NEW_VIDEO_HOURS, 0)
               if r["id"] not in sent]

    # 2) Benzer kanallarda tutan videolar + yeni kanal keşfi
    disc_videos = tb.get_details(discover_ids())
    disc = [r for r in rows_from(disc_videos, DISCOVER_HOURS, DISCOVER_MIN_VIEWS)
            if r["id"] not in sent and r["id"] not in {w["id"] for w in watched}][:15]
    for v in disc_videos:
        ch = v["snippet"]["channelId"]
        if (int(v["statistics"].get("viewCount", 0)) >= ADD_CHANNEL_VIEWS
                and ch not in watch and len(watch) < MAX_WATCH):
            watch[ch] = {"name": v["snippet"]["channelTitle"], "handle": ""}
            print("Takibe eklendi:", v["snippet"]["channelTitle"])

    if watched:
        tb.send_telegram(watched, "📺 Takip ettiğin kanalların yeni videoları")
    if disc:
        tb.send_telegram(disc, "🔥 Benzer kanallarda tutan videolar")
    if watched or disc:
        tb.tg_send(HOW_TO)

    now_ts = datetime.now(timezone.utc).timestamp()
    for r in watched + disc:
        sent[r["id"]] = now_ts
    tb.save_sent(sent, SENT_ROBLOX)
    save_watch(watch)
    print(f"{len(watch)} kanal takipte, {len(watched)} yeni + {len(disc)} benzer video gönderildi.")
