import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from duckduckgo_search import DDGS
import google.generativeai as genai
from flask import Flask
from threading import Thread

# ==========================================
# 👇 ŞİFRELERİNİ BURAYA YAPIŞTIR 👇
TG_TOKEN = "8559922950:AAG4n_6ef6KGhpBlKNS-wul8799l3_5IWns"
GEMINI_KEY = "AIzaSyCCCuG0WWoRYLVpiNJ720HnxJogIIIlKqI"
# ==========================================

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
    try:
        genai.configure(api_key=GEMINI_KEY)
        # En garanti çalışan modeli bulmaya çalış
        found = False
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'flash' in m.name:
                    model = genai.GenerativeModel(m.name)
                    found = True
                    break
        if not found:
            model = genai.GenerativeModel('gemini-pro')
    except:
        print("Model hatasi")

def search_web(query):
    try:
        with DDGS() as ddgs:
            # Bulut sunucularda rate-limit yememek için daha az istek
            ddg_results = list(ddgs.text(query, region='tr-tr', max_results=2))
            results = []
            for r in ddg_results:
                results.append(f"- {r['title']}: {r['body']}")
            return results
    except:
        return []

def ask_gemini(claim, evidences):
    if not model: return "Yapay zeka hazir degil."
    if not evidences: return "Kanit bulamadim."
    
    text = "\n".join(evidences)
    prompt = f"İddia: {claim}\nKanıtlar:\n{text}\n\nBu iddia doğru mu? (Evet/Hayır/Belirsiz). Kısa açıkla."
    try:
        response = model.generate_content(prompt)
        return response.text
    except:
        return "Yapay zeka hatasi."

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    if len(msg) < 5: return
    status = await update.message.reply_text("⏳")
    evidences = await asyncio.to_thread(search_web, msg)
    answer = await asyncio.to_thread(ask_gemini, msg, evidences)
    await status.edit_text(answer)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ben buluttayim! 7/24 calisiyorum.")

def main():
    keep_alive() # Web sitesini baslat
    setup_ai()
    app = ApplicationBuilder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
```

Sağ üstten **"Commit changes"** (Yeşil buton) diyerek kaydet.

---

#### B) `requirements.txt` Dosyası
Botun hangi kütüphanelere ihtiyacı olduğunu sunucuya söylememiz lazım.
1.  GitHub'da dosyaların olduğu ana ekrana dön.
2.  Yine **"Add file"** -> **"Create new file"** de.
3.  Dosya adına `requirements.txt` yaz.
4.  İçine tam olarak şunları yapıştır:

```text
python-telegram-bot
duckduckgo-search
google-generativeai
flask
