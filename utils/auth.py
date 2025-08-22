import os
import gspread
from google.oauth2 import service_account
from google.auth import default as google_auth_default

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
CREDS_FILE = '/secrets/SERVICE_ACCOUNT_JSON'

def get_gspread_client():
    if 'GCP_PROJECT' in os.environ:
        creds, _ = google_auth_default(scopes=SCOPES)
    else:
        creds = service_account.Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    return gspread.authorize(creds)

