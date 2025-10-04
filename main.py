import logging
from flask import Flask, request
from google.cloud import secretmanager
from robots import news_harvester, content_crafter, visual_styler, voice_smith, publisher

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

def get_secret(secret_id):
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/<GCP_PROJECT_ID>/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(name=name)
    return response.payload.data.decode("UTF-8")

@app.route("/", methods=["POST"])
def main():
    logging.info("🚀 GoNews otomasyonu tetiklendi")

    try:
        news_harvester.run()
        content_crafter.run()
        visual_styler.run()
        voice_smith.run()
        publisher.run()

        logging.info("✅ GoNews otomasyonu tamamlandı")
        return {"status": "success"}, 200

    except Exception as e:
        logging.error(f"❌ Hata oluştu: {e}")
        return {"status": "error", "message": str(e)}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)