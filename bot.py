import os
import re
import asyncio
import time
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from ddgs import DDGS  # duckduckgo_search yerine stabil paket
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

# --- ALAKA FİLTRESİ İÇİN STOPWORDS + KEYWORD ÇIKARMA ---
STOPWORDS_TR = {
    "mi","mı","mu","mü","ve","ile","da","de","ki","ama","fakat","gibi",
    "ne","nedir","doğru","yanlış","mıydı","miymiş","acaba","şey",
    "bu","şu","o","bir","hangi","kadar","kim","neden","nasıl",
    "olarak","için","çok","az","daha","en"
}

def extract_keywords(text, k=6):
    words = re.findall(r"[a-zA-ZçğıöşüÇĞİÖŞÜ0-9\-]+", text.lower())
    words = [w for w in words if w not in STOPWORDS_TR and len(w) > 2]
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    kws = sorted(freq.keys(), key=lambda x: freq[x], reverse=True)
    return kws[:k]

def relevance_score(claim, snippet):
    kws = extract_keywords(claim)
    if not kws:
        return 0.0
    s = snippet.lower()
    hit = sum(1 for kw in kws if kw in s)
    return hit / len(kws)
# -------------------------------------------------------

def pick_working_model():
    """
    generateContent destekleyen modelleri tarar,
    exp/experimental olanları sona atar,
    küçük ping ile çalışan ilk modeli seçer.
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
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]

    def sort_key(n):
        for i, p in enumerate(preferred):
            if p in n:
                return i
        return len(preferred) + 1

    stable.sort(key=sort_key)
    candidates = stable + exp

    for cand in candidates:
        try:
            m = genai.GenerativeModel(cand)
            r = m.generate_content(test_prompt)
            if (r.text or "").strip():
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
    """Gemini modelini otomatik çalışan bulup kurar."""
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
        print(f"Model kuruldu: {picked}")

    except Exception as e:
        print(f"Model hatasi: {e}")
        model = None

def clean_claim(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:200] if len(text) > 200 else text

def search_web(query):
    """
    1) Önce haberlerde ara
    2) Yetmezse query genişletip webde ara
    3) Hâlâ yetmezse sabit güvenilir sitelerden yedek ara
    + Alakasız sonuçları relevance_score ile ele
    """
    results = []
    try:
        with DDGS() as ddgs:
            # 1) Haber araması
            news_results = list(ddgs.news(
                query,
                region="tr-tr",
                safesearch="moderate",
                max_results=8
            ))

            for r in news_results:
                title = r.get("title", "")
                body = r.get("body", "")
                link = r.get("url") or r.get("href", "")
                source = r.get("source", "")
                snippet = f"{title} {body}"

                if relevance_score(query, snippet) >= 0.35:
                    results.append(f"- [{source}] {title}: {body} ({link})")

            # 2) Yetmezse genişletilmiş text arama
            if len(results) < 2:
                expanded = f"{query} doğru mu yanlış mı"
                text_results = list(ddgs.text(
                    expanded,
                    region="tr-tr",
                    safesearch="moderate",
                    max_results=6
                ))
                for r in text_results:
                    title = r.get("title", "")
                    body = r.get("body", "")
                    link = r.get("href") or r.get("link") or ""
                    snippet = f"{title} {body}"

                    if len(body) > 20 and relevance_score(query, snippet) >= 0.30:
                        results.append(f"- {title}: {body} ({link})")

            # 3) Hâlâ yoksa sabit kaynak yedeği
            if len(results) < 2:
                backup_q = f"{query} site:wikipedia.org OR site:teyit.org OR site:aa.com.tr"
                back_results = list(ddgs.text(
                    backup_q,
                    region="tr-tr",
                    safesearch="moderate",
                    max_results=6
                ))
                for r in back_results:
                    title = r.get("title", "")
                    body = r.get("body", "")
                    link = r.get("href") or r.get("link") or ""
                    snippet = f"{title} {body}"

                    if len(body) > 20 and relevance_score(query, snippet) >= 0.25:
                        results.append(f"- {title}: {body} ({link})")

        return results[:8]

    except Exception as e:
        print("Arama hatasi:", e)
        return []

def ask_gemini(claim, evidences):
    if not model:
        return "Yapay zeka baslatilamadi."
    if not evidences:
        return "BELIRSIZ. Konuyla ilgili güvenilir ve alakalı kaynak bulunamadı."

    evidence_text = "\n".join(evidences)

    prompt = f"""
Sen profesyonel bir teyitçisin (fact-checker).
Sadece aşağıdaki HABER KAYNAKLARINA dayanarak iddiayı analiz et.
Kanıtta olmayan hiçbir şeyi ekleme.
Kanıtlar alakasızsa BELİRSİZ de.

İddia: "{claim}"

Bulunan Haberler/Kaynaklar:
{evidence_text}

CEVAP FORMATI:
Özet:
- (Haberlerden kısa maddeler)

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
        msg = str(e).lower()
        if "429" in msg or "quota" in msg or "rate" in msg:
            try:
                time.sleep(55)
                resp = model.generate_content(prompt)
                out = (resp.text or "").strip()
                return out if out else "Cevap üretilemedi."
            except Exception as e2:
                return f"Yapay zeka hatasi (limit): {e2}"
        return f"Yapay zeka hatasi: {e}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = clean_claim(update.message.text)
    if len(msg) < 5:
        return

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
