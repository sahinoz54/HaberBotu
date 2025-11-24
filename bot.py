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
    """Gemini modelini güvenli şekilde seçip kurar."""
    global model
    if not GEMINI_KEY:
        print("HATA: GEMINI_KEY Render ayarlarinda yok!")
        model = None
        return

    try:
        genai.configure(api_key=GEMINI_KEY)

        # Deneysel (exp) modelleri eledik
        preferred = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"]
        available = [
            m.name for m in genai.list_models()
            if "generateContent" in getattr(m, "supported_generation_methods", [])
        ]

        picked = next(
            (a for p in preferred for a in available if p in a and "exp" not in a),
            available[0] if available else None
        )

        if picked:
            model = genai.GenerativeModel(picked)
            print(f"Model kuruldu: {picked}")
        else:
            print("HATA: Uygun model bulunamadi.")
            model = None

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
    """ÖNCE HABERLERDE, SONRA WEBDE ARA"""
    results = []
    try:
        with DDGS() as ddgs:
            # 1. Aşama: SADECE HABERLERİ ARA (News Search)
            # Bu mod saçma siteleri getirmez, sadece gazeteleri getirir.
            news_results = list(ddgs.news(
                query,
                region="tr-tr",
                safesearch="moderate",
                max_results=5
            ))
            
            for r in news_results:
                title = r.get("title", "")
                body = r.get("body", "") # Haberlerde body genelde özet olur
                link = r.get("url") or r.get("href", "")
                source = r.get("source", "") # Haber kaynağı (Hürriyet, BBC vs.)
                
                results.append(f"- [{source}] {title}: {body} ({link})")

            # 2. Aşama: Eğer haber çıkmazsa normal aramaya dön (Yedek)
            if len(results) < 2:
                text_results = list(ddgs.text(
                    f"{query} haber",
                    region="tr-tr",
                    safesearch="moderate",
                    max_results=3
                ))
                for r in text_results:
                    title = r.get("title", "")
                    body = r.get("body", "")
                    link = r.get("href", "")
                    if len(body) > 20:
                        results.append(f"- {title}: {body} ({link})")

        return results[:8] # En iyi 8 sonucu döndür
    except Exception as e:
        print("Arama hatasi:", e)
        return []

def ask_gemini(claim, evidences):
    if not model: return "Yapay zeka baslatilamadi."
    if not evidences: return "BELIRSIZ. Konuyla ilgili güvenilir haber bulunamadı."

    evidence_text = "\n".join(evidences)

    prompt = f"""
Sen profesyonel bir teyitçisin (fact-checker). 
Sadece aşağıdaki **HABER KAYNAKLARINA** dayanarak iddiayı analiz et.

İddia: "{claim}"

Bulunan Haberler/Kaynaklar:
{evidence_text}

GÖREVİN:
1. Kaynaklar iddiayı doğruluyor mu, yalanlıyor mu yoksa konuyla alakasız mı?
2. Eğer kaynaklar alakasızsa (örn: banka reklamı vs.) "BELİRSİZ" de.
3. Asla kendi fikrini katma.

CEVAP FORMATI:
Özet:
- (Haberlerden kısa maddeler)

Hüküm: EVET / HAYIR / BELİRSİZ / İDDİA (Sadece iddia aşamasında ise)
Gerekçe: (1 cümle)
Kaynaklar:
1) ...
"""
    try:
        resp = model.generate_content(prompt)
        out = (resp.text or "").strip()
        if not out: return "Cevap üretilemedi."
        return out
    except Exception as e:
        return f"Yapay zeka hatasi: {e}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = clean_claim(update.message.text)
    if len(msg) < 5: return

    status = await update.message.reply_text("📰 Haber kaynakları taranıyor...")

    evidences = await asyncio.to_thread(search_web, msg)
    answer = await asyncio.to_thread(ask_gemini, msg, evidences)

    try:
        await status.edit_text(answer, disable_web_page_preview=True)
    except:
        await update.message.reply_text(answer, disable_web_page_preview=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hazırım! Bir iddia veya haber başlığı yaz.")

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
