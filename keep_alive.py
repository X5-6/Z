from flask import Flask
from threading import Thread
import os
import logging

# Create a small, focused health app
app = Flask("neveroff-health")

@app.route("/")
def index():
    # Playful health response for uptime monitors
    return "🎉✨ neveroff: alive, buzzing, and smiling! 😊💪 Heartbeat strong — online forever! 🚀🔋", 200

def run(port: int):
    """Run the Flask app bound to the provided port (blocking)."""
    # Disable Flask's debug reloader and bind on all interfaces
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def keep_alive(port: int = 8080):
    """Start the health webserver in a daemon thread. Accepts port argument."""
    try:
        thread = Thread(target=run, args=(int(port),), daemon=True)
        thread.start()
    except Exception as e:
        # Use logging if available; avoid crashing the main app on errors here.
        try:
            import logging as _logging
            _logging.getLogger("neveroff").exception("keep_alive.start failed: %s", e)
        except Exception:
            # last-resort print if logging not configured
            print(f"[keep_alive] failed to start: {e}")
