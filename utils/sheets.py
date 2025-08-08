import datetime
import os # EKLENDİ
from utils.auth import get_gspread_client

SHEET_ID = os.environ.get('GOOGLE_SHEET_ID') # DEĞİŞTİ
LOG_TAB = 'Logs'

def log_to_sheet(robot_name, status, notes):
    gc = get_gspread_client() # Kimlik doğrulamayı artık bu fonksiyon hallediyor
    worksheet = gc.open_by_key(SHEET_ID).worksheet(LOG_TAB)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    worksheet.append_row([now, robot_name, status, notes])

def get_news_rows(sheet_id, tab_name):
    gc = get_gspread_client()
    worksheet = gc.open_by_key(sheet_id).worksheet(tab_name)
    records = worksheet.get_all_records()
    for i, row in enumerate(records, start=2):
        row['row_index'] = i
    return records

def update_news_row(sheet_id, tab_name, row_index, updates: dict):
    gc = get_gspread_client()
    worksheet = gc.open_by_key(sheet_id).worksheet(tab_name)
    headers = worksheet.row_values(1)
    for key, value in updates.items():
        if key in headers:
            col_num = headers.index(key) + 1
            worksheet.update_cell(row_index, col_num, value)