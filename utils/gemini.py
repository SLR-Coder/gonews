import google.generativeai as genai
import os

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY ortam değişkeni bulunamadı!")

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-2.5-pro")

def generate_title(prompt):
    response = model.generate_content(prompt)
    return response.text.strip()

def generate_summary(prompt):
    response = model.generate_content(prompt)
    return response.text.strip()

def generate_long_text(prompt):
    response = model.generate_content(prompt)
    return response.text.strip()
