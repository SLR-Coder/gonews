import datetime
import gspread
from google.oauth2.service_account import Credentials
from utils.gemini import generate_title, generate_summary, generate_long_text
from utils.duplicate import is_duplicate
import time

SHEET_ID = '1OZJc3ZapwvzWRfiflA1ElFjAr_0fbYiBw1Lerf4Bbzc'
NEWS_TAB = 'News'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
CREDS_FILE = 'service_account.json'

COL_ORG_TITLE = 6    # F
COL_ORG_LINK = 7     # G
COL_CATEGORY = 3     # C
COL_NEWTITLE = 8     # H
COL_SUMMARY = 9      # I
COL_LONGTEXT = 10    # J
COL_IMAGE = 11       # K
COL_STATUS = 19      # S

# ---- Kategoriye Özel GÜNCEL Prompt Ayarları ----
CATEGORY_PROMPTS = {
    "teknoloji": {
        "title": """
Aşağıdaki teknoloji haberinin başlığını ve özetini dikkatlice oku. Eğer başlık İngilizce ise önce Türkçeye çevir, sonra ilgi çekici ve kısa bir başlık üret. Sadece Türkçe başlık yaz, başka bilgi ekleme.
Başlık: {org_title}
Link: {org_link}
""",
        "summary": """
Haberi tamamen oku, eğer içerik İngilizce ise Türkçeye çevir. En önemli yeniliği, gelişmeyi 2-3 cümleyle sade ve özgün şekilde özetle. Sadece Türkçe özet yaz, başka cümle ekleme.
Başlık: {org_title}
Link: {org_link}
""",
        "longtext": """
Haberi baştan sona oku, İngilizce ise Türkçeye çevir. Teknik detaylarını ve gelişmelerini 4–6 paragraf özgün Türkçe metinle baştan yaz. Yalnızca haber metnini üret, giriş/gelişme/sonuç yapabilirsin. Açıklama ekleme.
Başlık: {org_title}
Link: {org_link}
"""
    },
    "spor": {
        "title": """
Aşağıdaki spor haberinin başlığını ve özetini dikkatlice oku. İngilizce ise önce Türkçeye çevir, ardından heyecanlı ve kısa Türkçe bir başlık üret. Sadece başlık yaz.
Başlık: {org_title}
Link: {org_link}
""",
        "summary": """
Spor haberini oku, İngilizceyse çevir. Maç sonucu, yıldız oyuncu veya önemli anı 2-3 cümlede Türkçe özetle. Sadece özet yaz.
Başlık: {org_title}
Link: {org_link}
""",
        "longtext": """
Spor haberini baştan sona incele, gerekiyorsa çevir. Önemli anları, sonuçları ve etkisini 4–6 paragraf Türkçe, özgün şekilde yaz. Sadece haberin detaylarını anlat.
Başlık: {org_title}
Link: {org_link}
"""
    },
    "ekonomi": {
        "title": """
Ekonomi haberinin başlığını oku, İngilizce ise çevir. Sade ve dikkat çekici Türkçe başlık yaz. Sadece başlık.
Başlık: {org_title}
Link: {org_link}
""",
        "summary": """
Ekonomi haberini incele, İngilizceyse çevir. En önemli gelişmeyi ve etkisini 2-3 cümleyle Türkçe özetle. Sadece özet yaz.
Başlık: {org_title}
Link: {org_link}
""",
        "longtext": """
Ekonomi haberinin arka planını, sonuçlarını ve piyasa etkilerini 4-6 paragraf halinde özgün Türkçe metinle yaz. Yalnızca haber metni üret.
Başlık: {org_title}
Link: {org_link}
"""
    },
    "kultur": {
        "title": """
Kültür/sanat haberinin başlığını oku, İngilizceyse Türkçeye çevir ve merak uyandıran kısa bir başlık üret. Sadece başlık.
Başlık: {org_title}
Link: {org_link}
""",
        "summary": """
Kültür/sanat haberini dikkatlice incele, gerekiyorsa Türkçeye çevir. Eser, sanatçı veya etkinliği 2-3 cümlede özgün şekilde özetle. Sadece özet.
Başlık: {org_title}
Link: {org_link}
""",
        "longtext": """
Haberi oku, İngilizceyse çevir. Sanatçının/etkinliğin öyküsünü ve kültürel etkisini 4-6 paragraf özgün Türkçe metinle anlat. Sadece haber metni.
Başlık: {org_title}
Link: {org_link}
"""
    },
    "dunya_gundemi": {
        "title": """
Dünya gündemi haberinin başlığını oku, İngilizce ise çevir ve küresel önemi vurgulayan kısa bir Türkçe başlık üret. Sadece başlık.
Başlık: {org_title}
Link: {org_link}
""",
        "summary": """
Dünya haberini oku, İngilizceyse çevir. En kritik gelişmeyi ve etkisini 2-3 cümleyle Türkçe özetle. Sadece özet.
Başlık: {org_title}
Link: {org_link}
""",
        "longtext": """
Dünya gündemi haberini detaylıca incele, İngilizceyse çevir. Nedeni, sonucu ve etkilerini 4-6 paragraf Türkçe metinle yaz. Sadece haber metni üret.
Başlık: {org_title}
Link: {org_link}
"""
    },
    "turkiye_gundemi": {
        "title": """
Türkiye gündemi haberinin başlığını incele, İngilizceyse çevir. Toplumsal veya güncel önemi vurgulayan kısa bir Türkçe başlık yaz. Sadece başlık.
Başlık: {org_title}
Link: {org_link}
""",
        "summary": """
Türkiye haberini oku, İngilizceyse çevir. Gelişmenin ülke genelindeki etkisini 2-3 cümlede Türkçe özetle. Sadece özet.
Başlık: {org_title}
Link: {org_link}
""",
        "longtext": """
Türkiye gündemi haberini baştan sona oku, gerekiyorsa çevir. Siyasi/ekonomik/toplumsal yönleriyle 4-6 paragraf Türkçe özgün haber metni yaz. Sadece haber metni.
Başlık: {org_title}
Link: {org_link}
"""
    },
    "sondakika": {
        "title": """
Son dakika haberinin başlığını dikkatlice oku, İngilizce ise çevir. Aciliyet duygusu taşıyan kısa ve dikkat çekici bir Türkçe başlık yaz. Sadece başlık.
Başlık: {org_title}
Link: {org_link}
""",
        "summary": """
Son dakika gelişmesini incele, İngilizceyse çevir. En önemli bilgiyi 2-3 cümleyle özetle. Sadece özet.
Başlık: {org_title}
Link: {org_link}
""",
        "longtext": """
Son dakika gelişmesinin tüm detaylarını, önemini ve etkilerini 4-6 paragraf hızlı ve net Türkçe metinle yaz. Sadece haber metni.
Başlık: {org_title}
Link: {org_link}
"""
    },
    "fotogaleri": {
        "title": """
Foto galeri başlığını oku, İngilizceyse çevir. Galerinin konusuna ve görsellere uygun ilgi çekici Türkçe başlık üret. Sadece başlık.
Başlık: {org_title}
Link: {org_link}
""",
        "summary": """
Foto galeriyi incele, gerekiyorsa çevir. Temasını ve öne çıkan kareleri 2-3 cümleyle Türkçe özetle. Sadece özet.
Başlık: {org_title}
Link: {org_link}
""",
        "longtext": """
Foto galerinin hikayesini, görsellerin anlamını ve olayın detaylarını 4-6 paragraf Türkçe haber metniyle yaz. Sadece haber metni üret.
Başlık: {org_title}
Link: {org_link}
"""
    },
    "default": {
        "title": """
Aşağıdaki haberin başlığını ve özetini dikkatlice oku. İngilizceyse çevir, ardından kategoriye uygun, kısa ve dikkat çekici Türkçe başlık yaz. Sadece başlık.
Başlık: {org_title}
Link: {org_link}
""",
        "summary": """
Haberi incele, İngilizceyse çevir. En önemli gelişmeyi 2-3 cümleyle açık ve anlaşılır biçimde özetle. Sadece özet yaz.
Başlık: {org_title}
Link: {org_link}
""",
        "longtext": """
Haberi baştan sona oku, gerekiyorsa çevir. Önemli gelişmeleri ve etkilerini Türkçe 4-6 paragraf halinde kapsamlı ve özgün şekilde yaz. Sadece haber metni üret.
Başlık: {org_title}
Link: {org_link}
"""
    }
}

def get_unprocessed_news():
    credentials = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    gc = gspread.authorize(credentials)
    worksheet = gc.open_by_key(SHEET_ID).worksheet(NEWS_TAB)
    all_rows = worksheet.get_all_values()
    news_rows = []
    for i, row in enumerate(all_rows[1:], start=2):
        status = row[COL_STATUS-1].strip().lower()
        newtitle = row[COL_NEWTITLE-1].strip()
        summary = row[COL_SUMMARY-1].strip()
        longtext = row[COL_LONGTEXT-1].strip()
        image = row[COL_IMAGE-1].strip()
        if (
            status == "robot 1 başarılı" and
            not newtitle and not summary and not longtext and
            image
        ):
            news_rows.append({
                "row_index": i,
                "org_title": row[COL_ORG_TITLE-1],
                "org_link": row[COL_ORG_LINK-1],
                "category": row[COL_CATEGORY-1],
                "image": image
            })
    return news_rows, worksheet, all_rows

def get_existing_titles(all_rows):
    return [row[COL_NEWTITLE-1] for row in all_rows if row[COL_NEWTITLE-1].strip()]

def build_prompts(category, org_title, org_link):
    cat = category.lower()
    prompts = CATEGORY_PROMPTS.get(cat, CATEGORY_PROMPTS["default"])
    # Bütün promptlarda hem org_title hem org_link parametresini kullanalım:
    return (
        prompts["title"].format(org_title=org_title, org_link=org_link),
        prompts["summary"].format(org_title=org_title, org_link=org_link),
        prompts["longtext"].format(org_title=org_title, org_link=org_link)
    )

def process_news_row(news_row, worksheet, existing_titles):
    org_title = news_row["org_title"]
    org_link = news_row["org_link"]
    category = news_row["category"]

    title_prompt, summary_prompt, longtext_prompt = build_prompts(category, org_title, org_link)
    new_title = generate_title(title_prompt).strip()

    # --- AI Duplicate Detection ---
    if is_duplicate(new_title, existing_titles, threshold=0.85):
        worksheet.update(f"S{news_row['row_index']}", [["Robot 1 Başarılı / Robot 2 Önceden İşlenmiş"]])
        print(f"Duplicate: {new_title}")
        return

    try:
        summary = generate_summary(summary_prompt).strip()
        if not summary or "oluşturulamamıştır" in summary or "Lütfen linkin doğruluğunu" in summary:
            summary = "Özet üretilemedi."
    except Exception as e:
        summary = f"Özet üretilemedi. Hata: {e}"
    
    try:
        long_text = generate_long_text(longtext_prompt).strip()
        if not long_text or "oluşturulamamıştır" in long_text:
            long_text = "Haber metni üretilemedi."
    except Exception as e:
        long_text = f"Haber metni üretilemedi. Hata: {e}"

    worksheet.update(
        f"H{news_row['row_index']}:J{news_row['row_index']}",
        [[new_title, summary, long_text]]
    )
    time.sleep(1)
    worksheet.update(
        f"S{news_row['row_index']}",
        [[f"Robot 1 Başarılı / Robot 2 Başarılı"]]
    )
    time.sleep(1)

def run():
    news_rows, worksheet, all_rows = get_unprocessed_news()
    existing_titles = get_existing_titles(all_rows)
    print(f"{len(news_rows)} adet haber işleniyor...")

    for news_row in news_rows:
        try:
            process_news_row(news_row, worksheet, existing_titles)
        except Exception as e:
            worksheet.update(
                f"S{news_row['row_index']}",
                [[f"Robot 1 Başarılı / Robot 2 Hata: {str(e)}"]]
            )
            print(f"Hata: Satır {news_row['row_index']}: {str(e)}")

    print("ContentCrafter tamamlandı.")

if __name__ == "__main__":
    run()