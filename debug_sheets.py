import gspread
from google.oauth2.service_account import Credentials
import os
from dotenv import load_dotenv

# Gerekli dosyaları ve değişkenleri yüklüyoruz
load_dotenv()
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
CREDS_FILE = 'service_account.json'
TARGET_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID')

print("--- İzin Testi Başlatılıyor (Versiyon 2) ---")
print(f"Hedeflenen Sheet ID: {TARGET_SHEET_ID}")

try:
    print("\n1. Kimlik doğrulanıyor...")
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    print("✅ Kimlik doğrulama başarılı!")

    print("\n2. Bu hesabın erişebildiği TÜM spreadsheet'ler listeleniyor...")
    # --- DÜZELTME BURADA ---
    # Doğru fonksiyon adı 'list_spreadsheet_files'
    spreadsheets_meta = gc.list_spreadsheet_files()

    if not spreadsheets_meta:
        print("⚠️ UYARI: Bu hizmet hesabı HİÇBİR spreadsheet'e erişemiyor.")
    else:
        print("✅ Erişilebilen spreadsheet'ler:")
        found = False
        for sheet_meta in spreadsheets_meta:
            print(f"  - Ad: '{sheet_meta['name']}', ID: '{sheet_meta['id']}'")
            if sheet_meta['id'] == TARGET_SHEET_ID:
                found = True

        if found:
            print("\n✅ SONUÇ: Hedeflenen spreadsheet, erişilebilenler listesinde bulundu!")
        else:
            print("\n❌ HATA: Hedeflenen spreadsheet, erişilebilenler listesinde YOK.")

    print("\n3. Hedeflenen spreadsheet'i doğrudan ID ile açma deneniyor...")
    sheet = gc.open_by_key(TARGET_SHEET_ID)
    print(f"✅ SONUÇ: '{sheet.title}' adlı spreadsheet ID ile başarıyla açılabildi.")

except gspread.exceptions.SpreadsheetNotFound:
    print("\n❌ KESİN HATA: gspread.exceptions.SpreadsheetNotFound")
    print("Bu, ya ID'nin yanlış olduğu ya da paylaşım izninin henüz sisteme yansımadığı anlamına gelir.")
except Exception as e:
    print(f"\n❌ BEKLENMEDİK HATA: {type(e).__name__}")
    print(f"Detaylar: {e}")

print("\n--- Test Tamamlandı ---")
