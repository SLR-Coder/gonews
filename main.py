from flask import Flask, request
import subprocess
import os

app = Flask(__name__)

@app.route("/", methods=["GET"])
def run_all_bots():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        robots_dir = os.path.join(base_dir, "robots")

        bots = [
            "news_harvester.py",
            "content_crafter.py",
            "visual_styler.py",
            "voice_smith.py",
            "podcast_duo.py",
            "video_forge.py",
            "publisher_bot.py"
        ]

        logs = []

        for bot in bots:
            bot_path = os.path.join(robots_dir, bot)
            if os.path.isfile(bot_path):
                result = subprocess.run(["python3", bot_path], capture_output=True, text=True)
                logs.append(f"✅ {bot} çalıştı\n{result.stdout}")
                if result.stderr:
                    logs.append(f"⚠️ {bot} hata verdi:\n{result.stderr}")
            else:
                logs.append(f"⛔ {bot} bulunamadı")

        return "\n\n".join(logs), 200

    except Exception as e:
        return f"💥 Ana betik hata verdi: {str(e)}", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
