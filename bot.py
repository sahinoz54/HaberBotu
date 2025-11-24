import os
import re
import asyncio
import time
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# --- DDG IMPORT FIX (SEÇENEK B – İKİSİNİ DE DESTEKLER) ---
try:
    from ddgs import DDGS  # yeni paket
    print("DDGS paketi kullanılıyor.")
except ModuleNotFoundError:
    from duckduckgo_search import DDGS  # eski paket
    print("duckduckgo_search kullanılıyor (yedek).")

import google.generativeai as genai
from flask import Flask
from threading import Thread

# --- ŞİFRELER ---
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_KEY")
# ---------------------------------------------------

# --- UYKU ÖNLEME SERVER ---
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Bot çalışıyor."

def run_web():
    web_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    Thread(target=run_web, daemon=True).start()
# ---------------------------------------------------

model = None

# --- ALAKA ANALİZİ ---
STOPWORDS_TR = {
    "mi","mı","mu","mü","ve","ile","da","de","ki","ama","fakat","gibi",
    "ne","nedir","doğru","yanlış","mıydı","miymiş","acaba","şey",
    "bu","şu","o","bir","hangi","kadar","kim","neden","nasıl",
    "olarak","için","çok","az","daha","en"
}

def extract_keywords(text, limit=6):
    words = re.findall(r"[a-zA-ZçğıöşüÇĞİÖŞÜ0-9\-]+", text.lower())
    words = [w for w in words if w not in STOPWORDS_TR and len(w) > 2]
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    return sorted(freq.keys(), key=lambda x: freq[x], reverse=True)[:limit]

def relevance_score(claim, snippet):
    kws = extract_keywords(claim)
    if not kws:
        return 0.0
    s = snippet.lower()
    score = sum(1 for kw in kws if kw in s)
    return score / len(kws)

# --- MODEL SEÇİCİ ---
def pick_working_model():
    test_prompt = "Sadece OK yaz."
    try:
        models = [
            m.name for m in genai.list_models()
            if "generateContent" in getattr(m, "supported_generation_methods", [])
        ]
    except:
        return None

    stable = []
    exp = []
    for m in models:
        if "exp" in m.lower() or "experimental" in m.lower():
            exp.append(m)
        else:
            stable.append(m)

    preferred = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro"
    ]

    def sort_key(name):
        for i, p in enumerate(preferred):
            if p in name:
                return i
        return len(preferred) + 1

    stable.sort(key=sort_key)
    candidates = stable + exp

    # Modelleri tek tek dener
    for cand in candidates:
        try:
            m = genai.GenerativeModel(cand)
            r = m.generate_content(test_prompt)
            if (r.text or "").strip():
                print("Kullanılabilir model:", cand)
                return cand
        except Exception as e:
            print("Model geçildi:", cand, "|", e)
            continue

    return None

def setup_ai():
    global model
    if not GEMINI_KEY:
        print("HATA: GEMINI_KEY yok.")
        return

    genai.configure(api_key=GEMINI_KEY)
    picked = pick_working_model()

    if not picked:
        print("HATA: Çalışır model bulunamadı.")
        return

    model = genai.GenerativeModel(picked)
    print("Model aktif:", picked)

# --- METİN TEMİZLEME ---
def clean_claim(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:200]

# --- AKILLI ARAMA ---
def search_web(query):
    results = []
    try:
        with DDGS() as ddgs:
            # 1) Haber Araması
            news = list(ddgs.news(query, region="tr-tr", safesearch="moderate", max_results=8))
            for r in news:
                title = r.get("title", "")
                body = r.get("body", "")
                link = r.get("url") or r.get("href", "")
                src = r.get("source", "")
                snippet = title + " " + body
                if relevance_score(query, snippet) >= 0.35:
                    results.append(f"- [{src}] {title}: {body} ({link})")

            # 2) Genişletilmiş Arama (Kanıt yetersizse)
            if len(results) < 2:
                text_q = f"{query} doğru mu yanlış mı"
                tsearch = list(ddgs.text(text_q, region="tr-tr", safesearch="moderate", max_results=6))
                for r in tsearch:
                    title = r.get("title", "")
                    body = r.get("body", "")
                    link = r.get("href") or r.get("link", "")
                    snippet = title + " " + body
                    if len(body) > 20 and relevance_score(query, snippet) >= 0.30:
                        results.append(f"- {title}: {body} ({link})")

            # 3) Sabit Güvenilir Kaynaklar
            if len(results) < 2:
                bq = f"{query} site:wikipedia.org OR site:teyit.org OR site:aa.com.tr"
                trust = list(ddgs.text(bq, region="tr-tr", safesearch="moderate", max_results=6))
                for r in trust:
                    title = r.get("title", "")
                    body = r.get("body", "")
                    link = r.get("href", "")
                    snippet = title + " " + body
                    if len(body) > 20 and relevance_score(query, snippet) >= 0.25:
                        results.append(f"- {title}: {body} ({link})")

        return results[:8]

    except Exception as e:
        print("Arama Hatası:", e)
        return []

# --- YAPAY ZEKA CEVABI ---
def ask_gemini(claim, evidences):
    if not model:
        return "Yapay zeka başlatılamadı."
    if not evidences:
        return "BELİRSİZ. Konuyla ilgili güvenilir haber bulunamadı."

    ev_text = "\n".join(evidences)

    prompt = f"""
Sen profesyonel bir teyitçisin.
Sadece aşağıdaki kanıtlara dayan.
Kanıt alakasızsa BELİRSİZ de.

İddia: "{claim}"

Kanıtlar:
{ev_text}

Cevap Formatı:
Özet:
- ...

Hüküm: EVET / HAYIR / BELİRSİZ / İDDİA
Gerekçe: (1 cümle)
Kaynaklar:
1) ...
2) ...
"""
    try:
        resp = model.generate_content(prompt)
        out = (resp.text or "").strip()
        if out:
            return out
        return "Cevap üretilemedi."

    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            time.sleep(60)
            try:
                resp = model.generate_content(prompt)
                return (resp.text or "").strip()
            except:
                return "Yapay zeka hatası (limit aşıldı)."
        return f"Yapay zeka hatası: {e}"

# --- MESAJ HANDLER ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = clean_claim(update.message.text)
    if len(msg) < 3:
        return

    status = await update.message.reply_text("📰 Haber kaynakları taranıyor...")

    evidences = await asyncio.to_thread(search_web, msg)
    answer = await asyncio.to_thread(ask_gemini, msg, evidences)

    try:
        await status.edit_text(answer, disable_web_page_preview=True)
    except:
        await update.message.reply_text(answer, disable_web_page_preview=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hazırım! Bir iddia yaz kanka.")

# --- ANA ÇALIŞMA ---
def main():
    if not TG_TOKEN:
        print("HATA: TELEGRAM_BOT_TOKEN yok!")
        return

    keep_alive()
    setup_ai()

    app = ApplicationBuilder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
