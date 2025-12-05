from flask import Flask
from threading import Thread
import os

app = Flask("neveroff-health")

@app.route("/")
def index():
    # رسالة مرحة ومليئة بالحيوية كما طُلب
    return "🎉✨ neveroff says: I'm alive, buzzing, and smiling! 😊💪 Heartbeat strong, presence shining — let's stay online forever! 🚀🔋", 200

def run():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def keep_alive():
    thread = Thread(target=run, daemon=True)
    thread.start()
