FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bu ENV değişkeni sayesinde Cloud Run'dan gelen secret dosyayı tanıyabiliriz
ENV GOOGLE_APPLICATION_CREDENTIALS="/secrets/service_account.json"
