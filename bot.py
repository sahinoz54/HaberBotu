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

def setup_ai():
    """Tüm modelleri tarar, 'exp' olmayanı bulur ve seçer."""
    global model
    if not GEMINI_KEY:
        print("HATA: GEMINI_KEY Render ayarlarinda yok!")
        return

    try:
        genai.configure(api_key=GEMINI_KEY)
        
        print("Model aranıyor...")
        found_model_name = None

        # Hesabındaki tüm modelleri listele
        for m in genai.list_models():
            # Sadece metin üretebilenleri al
            if 'generateContent' in m.supported_generation_methods:
                name = m.name
                # 'exp' veya 'experimental' yazanlar kota hatası verir, onları atla
                if 'exp' in name.lower():
                    continue
                
                # Flash veya Pro görürsen direkt kap
                if 'flash' in name.lower() or 'pro' in name.lower():
                    found_model_name = name
                    break
        
        # Eğer yukarıdaki döngüde bulamazsa, exp olmayan ilk modeli al
        if not found_model_name:
             for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    if 'exp' not in m.name.lower():
                        found_model_name = m.name
                        break

        # Hâlâ bulamadıysa, varsayılanı zorla (son çare)
        if not found_model_name:
            found_model_name = "gemini-1.5-flash"

        print(f"✅ Seçilen Model: {found_model_name}")
        model = genai.GenerativeModel(found_model_name)

    except Exception as e:
        print(f"Model Kurulum Hatası: {e}")

def clean_claim(text: str) -> str:
    """Mesajı iddiaya çevirir."""
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > 200:
        text = text[:200]
    return text

def search_web(query):
    """DDG ile arama."""
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
            if len(body) < 30: continue
            results.append(f"- {title}: {body} ({link})")

        return results[:6]
    except Exception as e:
        print("DDG hata:", e)
        return []

def ask_gemini(claim, evidences):
    if not model: return "Yapay zeka baslatilamadi."
    if not evidences: return "BELIRSIZ. İnternette net kanit bulamadim."

    evidence_text = "\n".join(evidences)
    prompt = f"""
Sen bir fact-check asistanısın. SADECE aşağıdaki kanıtlara dayan.
İddia: "{claim}"
Kanıtlar:
{evidence_text}

Önce kanıtlardan özet çıkar, sonra hüküm ver.
Format:
Özet:
- ...
Hüküm: EVET/HAYIR/BELİRSİZ
Gerekçe: ...
Kaynaklar:
1) ...
"""
    try:
        resp = model.generate_content(prompt)
        return (resp.text or "").strip()
    except Exception as e:
        return f"Yapay zeka hatasi: {e}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = clean_claim(update.message.text)
    if len(msg) < 5: return

    status = await update.message.reply_text("⏳ Bakiyorum...")
    evidences = await asyncio.to_thread(search_web, msg)
    answer = await asyncio.to_thread(ask_gemini, msg, evidences)

    try:
        await status.edit_text(answer, disable_web_page_preview=True)
    except:
        await update.message.reply_text(answer, disable_web_page_preview=True)

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
