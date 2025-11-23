import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from duckduckgo_search import DDGS
import google.generativeai as genai
from flask import Flask
from threading import Thread

# --- ŞİFRELER ---
# Telegram Token'ın burada kalabilir (sorun yok)
TG_TOKEN = "8559922950:AAG4n_6ef6KGhpBlKNS-wul8799l3_5IWns"

# Gemini Key'i ARTIK Render'ın kasasından çekiyoruz!
GEMINI_KEY = os.environ.get("GEMINI_KEY")
# ----------------

# --- SAHTE WEB SİTESİ (Render uyumasın diye) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot Calisiyor! Ben buradayim."

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()
# ---------------------------------------------

model = None

def setup_ai():
    global model
    # Eğer kasada şifre yoksa hata ver
    if not GEMINI_KEY:
        print("HATA: Render Environment ayarlarinda GEMINI_KEY yok!")
        return

    try:
        genai.configure(api_key=GEMINI_KEY)
        found = False
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'flash' in m.name:
                    model = genai.GenerativeModel(m.name)
                    found = True
                    break
        if not found:
            model = genai.GenerativeModel('gemini-pro')
        print("Model kuruldu.")
    except Exception as e:
        print(f"Model hatasi: {e}")

def search_web(query):
    try:
        with DDGS() as ddgs:
            ddg_results = list(ddgs.text(query, region='tr-tr', max_results=2))
            results = []
            for r in ddg_results:
                results.append(f"- {r['title']}: {r['body']}")
            return results
    except:
        return []

def ask_gemini(claim, evidences):
    if not model: return "Yapay zeka şu an başlatılamadı."
    if not evidences: return "Kanıt bulunamadı."
    
    text = "\n".join(evidences)
    prompt = f"İddia: {claim}\nKanıtlar:\n{text}\n\nBu iddia doğru mu? (Evet/Hayır/Belirsiz). Kısa ve net açıkla."
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Yapay zeka hatası: {e}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    if len(msg) < 5: return
    status = await update.message.reply_text("⏳")
    evidences = await asyncio.to_thread(search_web, msg)
    answer = await asyncio.to_thread(ask_gemini, msg, evidences)
    await status.edit_text(answer)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("7/24 Aktifim! Şifrelerim güvende.")

def main():
    keep_alive()
    setup_ai()
    app = ApplicationBuilder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
