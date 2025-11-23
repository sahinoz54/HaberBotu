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

# --- ŞİFRELER ---
# Render Environment'a koy: TELEGRAM_BOT_TOKEN, GEMINI_KEY
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or "8559922950:AAG4n_6ef6KGhpBlKNS-wul8799l3_5IWns"
GEMINI_KEY = os.environ.get("GEMINI_KEY")
# ----------------

# --- SAHTE WEB SİTESİ (Render uyumasın diye) ---
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Bot çalışıyor."

def run_web():
    web_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run_web, daemon=True)
    t.start()
# ---------------------------------------------

model = None

def setup_ai():
    """Gemini modeli güvenli şekilde seçip kurar."""
    global model
    if not GEMINI_KEY:
        print("HATA: GEMINI_KEY yok!")
        return

    try:
        genai.configure(api_key=GEMINI_KEY)

        preferred = [
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "gemini-2.0-flash",
            "gemini-2.0-pro"
        ]

        available = []
        for m in genai.list_models():
            if "generateContent" in getattr(m, "supported_generation_methods", []):
                available.append(m.name)

        picked = None
        for p in preferred:
            for a in available:
                if p in a:
                    picked = a
                    break
            if picked:
                break

        if not picked and available:
            picked = available[0]

        if not picked:
            print("HATA: generateContent destekleyen model bulunamadı.")
            return

        model = genai.GenerativeModel(picked)
        print(f"Model kuruldu: {picked}")

    except Exception as e:
        print(f"Model hatası: {e}")

def clean_claim(text: str) -> str:
    """İddiayı sadeleştirir, aşırı uzunsa kısaltır."""
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > 200:
        text = text[:200]
    return text

def search_web(query):
    """DDG ile daha güçlü arama: daha fazla sonuç + kısa snippet eleme."""
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
    """2 aşamalı, kanıt-dışı konuşmayı engelleyen sıkı prompt."""
    if not model:
        return "Yapay zeka başlatılamadı."
    if not evidences:
        return "BELİRSİZ. İnternette net kanıt bulamadım."

    text = "\n".join(evidences)

    prompt = f"""
Sen bir fact-check asistanısın. SADECE aşağıdaki kanıtlara dayan.
Kanıtlarda olmayan hiçbir şeyi iddia etme, yorum uydurma.

İddia: "{claim}"

Kanıtlar:
{text}

Önce kanıtlardan iddiayla ilgili olan cümleleri 2-4 maddeyle özetle.
Sonra hüküm ver.

Cevap formatı aynen böyle olacak (başka hiçbir şey yazma):

Özet:
- ...
- ...

Hüküm: EVET/HAYIR/BELİRSİZ
Gerekçe: 1-2 cümle.

Kaynaklar:
1) link
2) link
"""

    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"Yapay zeka hatası: {e}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Düz mesajı iddia sayar, kanıt arar, gerekirse retry yapar."""
    msg = clean_claim(update.message.text)
    if len(msg) < 5:
        return

    status = await update.message.reply_text("⏳ Bakıyorum...")

    for attempt in range(2):  # 2 deneme
        evidences = await asyncio.to_thread(search_web, msg)
        answer = await asyncio.to_thread(ask_gemini, msg, evidences)

        # Format doğruysa kabul et
        if "Özet:" in answer and "Hüküm:" in answer:
            try:
                await status.edit_text(answer, disable_web_page_preview=True)
            except:
                await update.message.reply_text(answer, disable_web_page_preview=True)
            return

    # Hala saçmalıyorsa
    await status.edit_text(
        "BELİRSİZ. Kanıtlar net değil kanka, biraz daha açık yaz.",
        disable_web_page_preview=True
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Selam kanka 👋\n"
        "Bana bir iddia yaz, doğru mu yanlış mı bakıp söyleyeyim.\n"
        "Örn: 'Çin uzay savaşına hazırlanıyor mu?'"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Kullanım:\n"
        "- Direkt iddianı yaz.\n"
        "- Ben EVET/HAYIR/BELİRSİZ + kısa gerekçe + kaynak döneyim."
    )

def main():
    keep_alive()
    setup_ai()

    tg_app = ApplicationBuilder().token(TG_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CommandHandler("help", help_cmd))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Telegram bot çalışıyor...")
    tg_app.run_polling()

if __name__ == "__main__":
    main()
