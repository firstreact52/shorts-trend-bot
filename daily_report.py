"""
Günlük "Ne çekmeliyim?" raporu
- Son 24 saatte Amerika, Avrupa ve Türkiye'de patlayan Shorts'ları toplar (Hindistan hariç)
- Google'ın ücretsiz yapay zekası (Gemini) bunları inceleyip
  kopyalanabilir İÇERİK TARZLARINI ve senin çekebileceğin fikirleri yazar
- Sonucu Telegram'a gönderir
"""
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

import trend_bot as tb

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash"]

REPORT_REGIONS = [("US", "en"), ("TR", "tr"), ("GB", "en")]
REPORT_HOURS = 24
REPORT_MIN_VIEWS = 40_000
MAX_VIDEOS = 40


def collect():
    since = (datetime.now(timezone.utc) - timedelta(hours=REPORT_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = set()
    for region, lang in REPORT_REGIONS:
        data = tb.get("https://www.googleapis.com/youtube/v3/search", {
            "part": "id", "type": "video", "videoDuration": "short",
            "order": "viewCount", "publishedAfter": since, "maxResults": 50,
            "regionCode": region, "relevanceLanguage": lang,
        })
        for it in data.get("items", []):
            ids.add(it["id"]["videoId"])
    videos = tb.get_details(list(ids))
    countries = tb.channel_countries(videos) if videos else {}
    tb.MAX_AGE_HOURS = REPORT_HOURS
    tb.MIN_VIEWS = REPORT_MIN_VIEWS
    rows = tb.score(videos, countries)[:MAX_VIDEOS]
    by_id = {v["id"]: v for v in videos}
    for r in rows:
        v = by_id[r["id"]]
        r["seconds"] = tb.iso_to_seconds(v["contentDetails"].get("duration"))
        r["tags"] = (v["snippet"].get("tags") or [])[:8]
        r["desc"] = (v["snippet"].get("description") or "")[:150]
    return rows


PROMPT = """Sen bir YouTube Shorts strateji uzmanısın. Aşağıda son 24 saatte Amerika, Avrupa ve
Türkiye'de en hızlı izlenen Shorts videolarının listesi var (başlık, kanal, ülke, izlenme,
saatlik izlenme hızı, süre, etiketler, açıklama).

Görevin: Bu videolarda TEKRAR EDEN ve BAŞKASININ KOPYALAYABİLECEĞİ içerik tarzlarını (formatları) bul.
Okuyan kişi Türkiye'de yaşayan, tek başına çalışan, yüzünü göstermeden ve ücretsiz araçlarla
(yapay zeka seslendirme, hazır görüntü, ekran kaydı, basit kurgu) video üreten biri.

Kurallar:
- Başkasının videosunu yeniden yüklemeyi ASLA önerme (telif ihlali ve kanal kapanma riski).
  Sadece FORMATI, fikri, yapıyı kopyalamayı öner.
- Sade Türkçe yaz, teknik terim kullanma. Kısa cümleler.
- Markdown işareti (*, #, **) KULLANMA. Düz metin yaz. Emoji kullanabilirsin.

Şu yapıda yaz:

📊 BUGÜN NE TUTUYOR (2-3 cümlelik genel özet)

Sonra en kopyalanabilir 5 tarzı, en kolaydan zora sırala. Her biri için:
1) Tarzın adı
   Neden tutuyor: (1 cümle)
   Örnek: (listeden 1-2 video linki)
   Sen nasıl yaparsın: (2-3 kısa adım, ücretsiz araçlarla)
   Başlık kalıbı: (doldurulabilir örnek başlık)
   Zorluk: Kolay / Orta / Zor

En sonda:
🎯 BUGÜNÜN FİKRİ: Hemen bugün çekebileceği tek bir somut video fikri (konu + ilk 3 saniyede ne söylenecek).

Video listesi:
"""


def ask_gemini(text):
    body = json.dumps({"contents": [{"parts": [{"text": text}]}]}).encode()
    last_err = None
    for model in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        req = urllib.request.Request(url, body, {
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_KEY,
        })
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode())
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:  # sıradaki modeli dene
            last_err = e
    raise RuntimeError(f"Gemini yanıt vermedi: {last_err}")


def split_message(text, limit=3800):
    parts, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur += para + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


if __name__ == "__main__":
    rows = collect()
    tarih = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y")
    if not rows:
        tb.tg_send(f"🎬 Günlük İçerik Raporu — {tarih}\n\nBugün yeterli veri bulunamadı.")
        raise SystemExit
    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i}. {r['title']} | kanal: {r['channel']} [{r['country'] or '?'}] | "
            f"izlenme: {r['views']:,} | hız: {r['per_hour']:,}/saat | süre: {r['seconds']} sn | "
            f"etiketler: {', '.join(r['tags'])} | açıklama: {r['desc']!r} | {r['url']}"
        )
    answer = ask_gemini(PROMPT + "\n".join(lines))
    answer = answer.replace("**", "").replace("##", "").replace("# ", "")
    msgs = split_message(f"🎬 Günlük İçerik Raporu — {tarih}\n\n" + answer)
    for m in msgs:
        tb.tg_send(m)
    print(f"Rapor gönderildi ({len(rows)} video incelendi, {len(msgs)} mesaj).")
