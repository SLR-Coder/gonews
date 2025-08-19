FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Cloud Run Job entrypoint: main.py
CMD ["python", "main.py", "--robots", "all", "--json-logs"]
