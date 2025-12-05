from flask import Flask
from threading import Thread

app = Flask("neveroff-health")

@app.route("/")
def index():
    return "🎉✨ neveroff: alive, buzzing, and smiling! 😊💪 Heartbeat strong — online forever! 🚀🔋", 200

def run(port: int):
    # Bind to provided port, disable reloader for production
    app.run(host="0.0.0.0", port=int(port), debug=False, use_reloader=False)

def keep_alive(port: int = 8080):
    try:
        thread = Thread(target=run, args=(int(port),), daemon=True)
        thread.start()
    except Exception as e:
        # Non-fatal: log via print as fallback (logger may not be configured yet)
        try:
            import logging as _logging
            _logging.getLogger("neveroff").exception("keep_alive.start failed: %s", e)
        except Exception:
            print(f"[keep_alive] failed to start: {e}")
