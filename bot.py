import os
import re
import asyncio
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from duckduckgo_search import DDGS
import google.generativeai as genai
from flask import Flask
from threading import Thread
import time

# --- ŞİFRELER (Render Environment'tan geliyor) ---
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_KEY")
# ------------------------------------------------

# --- SAHTE WEB SİTESİ (Render uyumasın diye) ---
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Bot calisiyor."

def run_web():
    web_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run_web, daemon=True)
    t.start()
# ---------------------------------------------

model = None

def pick_working_model():
    """
    generateContent destekleyen modelleri tarar.
    Exp/deneysel (-exp, -experimental) olanları sona atar.
    Küçük bir ping ile çalışan ilk modeli döndürür.
    """
    test_prompt = "Sadece 'OK' yaz."

    try:
        models = [
            m.name for m in genai.list_models()
            if "generateContent" in getattr(m, "supported_generation_methods", [])
        ]
    except Exception as e:
        print("Model listesi cekilemedi:", e)
        return None

    if not models:
        return None

    stable, exp = [], []
    for name in models:
        low = name.lower()
        if "exp" in low or "experimental" in low:
            exp.append(name)
        else:
            stable.append(name)

    preferred = [
        "gemini-2.0-flash",
        "gemini-2.0-pro",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]

    def sort_key(n):
        for i, p in enumerate(preferred):
            if p in n:
                return i
        return len(preferred) + 1

    stable.sort(key=sort_key)
    candidates = stable + exp  # exp en sona

    for cand in candidates:
        try:
            m = genai.GenerativeModel(cand)
            r = m.generate_content(test_prompt)
            out = (r.text or "").strip()
            if out:
                print("Calisan model bulundu:", cand)
                return cand
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "quota" in msg or "rate" in msg:
                print("Model quota/limit nedeniyle gecildi:", cand)
                continue
            if "404" in msg or "not found" in msg or "permission" in msg:
                print("Model erisim/bulunamadi, gecildi:", cand)
                continue
            print("Model test hatasi, gecildi:", cand, "|", e)
            continue

    return None

def setup_ai():
    """Gemini modeli otomatik seçip kurar."""
    global model
    if not GEMINI_KEY:
        print("HATA: GEMINI_KEY Render ayarlarinda yok!")
        model = None
        return

    try:
        genai.configure(api_key=GEMINI_KEY)

        picked = pick_working_model()
        if not picked:
            print("HATA: Calisan uygun model bulunamadi.")
            model = None
            return

        model = genai.GenerativeModel(picked)
        print(f"Model kuruldu ve kilitlendi: {picked}")

    except Exception as e:
        print(f"Model hatasi: {e}")
        model = None

def clean_claim(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > 200:
        text = text[:200]
    return text

def search_web(query):
    try:
        with DDGS() as ddgs:
            ddg_results = list(ddgs.text(
                query,
                region="tr-tr",
                safesearch="moderate",
                max_results=6
            ))

        results = []
        for r in ddg_results:
            title = r.get("title", "")
            body = r.get("body", "")
            link = r.get("href") or r.get("link") or ""
            if len(body) < 30:
                continue
            results.append(f"- {title}: {body} ({link})")

        return results[:6]
    except Exception as e:
        print("DDG hata:", e)
        return []

def ask_gemini(claim, evidences):
    if not model:
        return "Yapay zeka baslatilamadi. (GEMINI_KEY/model sorunu)"
    if not evidences:
        return "BELIRSIZ. İnternette net kanit bulamadim."

    evidence_text = "\n".join(evidences)

    prompt = f"""
Sen bir fact-check asistanisin. SADECE aşağıdaki kanıtlara dayan.
Kanıtlarda olmayan hiçbir şeyi iddia etme, yorum uydurma.

İddia: "{claim}"

Kanıtlar:
{evidence_text}

Önce kanıtlardan iddiayla ilgili olan cümleleri 2-4 maddeyle özetle.
Sonra hüküm ver.

Cevap formatı aynen böyle olacak (başka hiçbir şey yazma):

Özet:
- ...
- ...

Hüküm: EVET/HAYIR/BELIRSIZ
Gerekçe: 1-2 cümle.

Kaynaklar:
1) link
2) link
"""

    try:
        resp = model.generate_content(prompt)
        out = (resp.text or "").strip()
        if out:
            return out
        return "Yapay zeka bos cevap döndü."
    except Exception as e:
        # 429 vs olursa kısa bekle + 1 retry
        msg = str(e).lower()
        if "429" in msg or "quota" in msg or "rate" in msg:
            try:
                time.sleep(55)
                resp = model.generate_content(prompt)
                out = (resp.text or "").strip()
                return out if out else "Yapay zeka bos cevap döndü."
            except Exception as e2:
                return f"Yapay zeka hatasi (limit): {e2}"
        return f"Yapay zeka hatasi: {e}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = clean_claim(update.message.text)
    if len(msg) < 5:
        return

    status = await update.message.reply_text("⏳ Bakiyorum...")

    for attempt in range(2):  # 2 deneme
        evidences = await asyncio.to_thread(search_web, msg)
        answer = await asyncio.to_thread(ask_gemini, msg, evidences)

        if answer.startswith("Yapay zeka") or answer.startswith("BELIRSIZ."):
            await status.edit_text(answer, disable_web_page_preview=True)
            return

        if "Özet:" in answer and "Hüküm:" in answer:
            await status.edit_text(answer, disable_web_page_preview=True)
            return

    await status.edit_text(
        "BELIRSIZ. Kanitlar net degil kanka, biraz daha acik yaz.",
        disable_web_page_preview=True
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hazirim! Idiani yaz.")

def main():
    if not TG_TOKEN:
        print("HATA: TELEGRAM_BOT_TOKEN Render ayarlarinda yok!")
        return

    keep_alive()
    setup_ai()

    tg_app = ApplicationBuilder().token(TG_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    tg_app.run_polling()

if __name__ == "__main__":
    main()
