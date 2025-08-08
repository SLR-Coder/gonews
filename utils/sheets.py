import datetime
import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = '1QMjWTYQHVFM8Ucygks_t8NHmLaZYMFuooreE9TjyIqc'
LOG_TAB = 'Logs'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
CREDS_FILE = 'service_account.json'

def log_to_sheet(robot_name, status, notes):
    credentials = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    gc = gspread.authorize(credentials)
    worksheet = gc.open_by_key(SHEET_ID).worksheet(LOG_TAB)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    worksheet.append_row([now, robot_name, status, notes])

def get_news_rows(sheet_id, tab_name):
    """
    Google Sheets'ten başlıklarla birlikte tüm satırları döndürür.
    Her satıra row_index ekler (2'den başlar).
    """
    credentials = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    gc = gspread.authorize(credentials)
    worksheet = gc.open_by_key(sheet_id).worksheet(tab_name)
    records = worksheet.get_all_records()
    for i, row in enumerate(records, start=2):  # Başlık satırı hariç
        row['row_index'] = i
    return records

def update_news_row(sheet_id, tab_name, row_index, updates: dict):
    """
    Belirtilen row_index'teki satırı verilen başlığa göre günceller.
    updates: {'Başlık': 'yeni başlık', 'Durum': 'xxx', 'Kısa Ses (MP3 Link)': 'audio/...'}
    """
    credentials = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    gc = gspread.authorize(credentials)
    worksheet = gc.open_by_key(sheet_id).worksheet(tab_name)
    headers = worksheet.row_values(1)
    for key, value in updates.items():
        if key in headers:
            col_num = headers.index(key) + 1  # Sütunlar 1 tabanlı
            worksheet.update_cell(row_index, col_num, value)


