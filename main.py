#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
neveroff - improved resilient gateway client
"""

import os
import sys
import json
import time
import threading
import traceback
import logging
import random
from typing import Optional

import requests
import websocket

from state_store import StateStore

# -----------------------
# Discord Gateway Opcodes
# -----------------------
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_PRESENCE_UPDATE = 3
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11
ACTIVITY_TYPE_CUSTOM = 4

# -----------------------
# Configuration (env vars)
# -----------------------
STATUS = os.getenv("status", "online")
CUSTOM_STATUS = os.getenv("custom_status", "")
EMOJI_NAME = os.getenv("emoji_name", "")
EMOJI_ID = os.getenv("emoji_id", None)
EMOJI_ANIMATED = os.getenv("emoji_animated", "False").lower() == "true"

TOKEN = os.getenv("token")
GATEWAY_URL = os.getenv("gateway_url", "wss://gateway.discord.gg/?v=9&encoding=json")
PERSIST_PATH = os.getenv("PERSIST_STATE_PATH", "/tmp/neveroff_state.json")
HEARTBEAT_TIMEOUT_MULTIPLIER = float(os.getenv("HEARTBEAT_TIMEOUT_MULTIPLIER", "2.0"))
RECONNECT_BASE_BACKOFF = float(os.getenv("RECONNECT_BASE_BACKOFF", "1.0"))
RECONNECT_MAX_BACKOFF = int(os.getenv("RECONNECT_MAX_BACKOFF", "60"))
RECONNECT_JITTER = os.getenv("RECONNECT_JITTER", "true").lower() == "true"
RECV_TIMEOUT = float(os.getenv("RECV_TIMEOUT", "15"))
SEND_TIMEOUT = float(os.getenv("SEND_TIMEOUT", "5"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

DEVICE_TYPE = os.getenv("DEVICE_TYPE", "pc")
DEVICE_MAP = {
    "pc": {"$os": "linux", "$browser": "chrome", "$device": "pc"},
    "chrome": {"$os": "linux", "$browser": "chrome", "$device": "pc"},
    "android": {"$os": "Android", "$browser": "Discord Android", "$device": "android"},
    "ios": {"$os": "iOS", "$browser": "Discord iOS", "$device": "iphone"},
    "playstation": {"$os": "PlayStation", "$browser": "PlayStation", "$device": "playstation"},
    "xbox": {"$os": "Xbox", "$browser": "Xbox", "$device": "xbox"},
    "browser": {"$os": "linux", "$browser": "firefox", "$device": "browser"},
}
IDENTITY_PROPS = DEVICE_MAP.get(DEVICE_TYPE, DEVICE_MAP["pc"])

if not TOKEN:
    print("[ERROR] Missing environment variable: token. Please add your Discord token.")
    sys.exit(1)

try:
    headers = {"Authorization": TOKEN, "Content-Type": "application/json"}
    resp = requests.get("https://discord.com/api/v9/users/@me", headers=headers, timeout=5)
    if resp.status_code != 200:
        if resp.status_code == 401:
            print("[ERROR] Your token is INVALID (HTTP 401 Unauthorized).")
        else:
            print(f"[ERROR] Token validation failed (status {resp.status_code}).")
        sys.exit(1)
    userinfo = resp.json()
    USERNAME = userinfo.get("username", "unknown")
    DISCRIMINATOR = userinfo.get("discriminator", "0000")
    USERID = userinfo.get("id", "unknown")
except Exception as e:
    print(f"[ERROR] Token validation failed due to network error: {e}")
    sys.exit(1)

logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("neveroff")

state = StateStore(PERSIST_PATH)
session_id: Optional[str] = state.get("session_id")
sequence = state.get("sequence")
ws = None
should_stop = threading.Event()
last_ack_timestamp = state.get("last_ack_timestamp") or time.time()

def safe_save_state():
    try:
        state.update({
            "session_id": session_id,
            "sequence": sequence,
            "last_ack_timestamp": last_ack_timestamp
        })
    except Exception as e:
        log.warning("Failed to persist state: %s", e)

def build_presence_payload(status=STATUS, custom=CUSTOM_STATUS, emoji_name=EMOJI_NAME, emoji_id=EMOJI_ID, emoji_animated=EMOJI_ANIMATED):
    activity = {
        "type": ACTIVITY_TYPE_CUSTOM,
        "state": custom,
        "name": "Custom Status",
        "id": "custom"
    }
    if emoji_name:
        emoji_obj = {"name": emoji_name}
        if emoji_id:
            emoji_obj["id"] = emoji_id
            emoji_obj["animated"] = bool(emoji_animated)
        activity["emoji"] = emoji_obj
    payload = {
        "op": OP_PRESENCE_UPDATE,
        "d": {
            "since": 0,
            "activities": [activity],
            "status": status,
            "afk": False
        }
    }
    return payload

def send_json(sock, obj) -> bool:
    try:
        sock.settimeout(SEND_TIMEOUT)
        return sock.send(json.dumps(obj)) is None or True
    except Exception as e:
        log.debug("send_json failed: %s", e)
        return False

def handle_dispatch(data):
    global sequence, session_id, last_ack_timestamp
    if "s" in data and data["s"] is not None:
        sequence = data["s"]
    t = data.get("t")
    d = data.get("d")
    if t == "READY":
        session_id = d.get("session_id", session_id)
        log.info("Session READY. Session ID: %s", (session_id[:8] + "...") if session_id else "none")
        safe_save_state()

def heartbeat_loop(sock, interval_ms, stop_event: threading.Event):
    global sequence, last_ack_timestamp
    interval = interval_ms / 1000.0
    while not stop_event.is_set():
        if stop_event.wait(interval):
            return
        try:
            now = time.time()
            if (now - (last_ack_timestamp or 0.0)) > (interval * HEARTBEAT_TIMEOUT_MULTIPLIER):
                log.warning("Missed heartbeat ACK for %.1fs (>%.1fs). Closing socket to force reconnect.", now - last_ack_timestamp, interval * HEARTBEAT_TIMEOUT_MULTIPLIER)
                try:
                    sock.close()
                finally:
                    return
            hb_payload = {"op": OP_HEARTBEAT, "d": sequence}
            if not send_json(sock, hb_payload):
                log.warning("Failed to send heartbeat; exiting heartbeat loop.")
                return
        except Exception:
            log.exception("Exception in heartbeat loop - exiting")
            return

def open_gateway_and_run():
    global ws, session_id, sequence, last_ack_timestamp
    backoff = RECONNECT_BASE_BACKOFF
    max_backoff = RECONNECT_MAX_BACKOFF
    RESET_SESSION_CODES = [4004, 4010, 4011, 4012, 4013, 4014]

    while not should_stop.is_set():
        hb_thread = None
        sock = None
        try:
            jitter = random.uniform(0.0, backoff * 0.3) if RECONNECT_JITTER else 0.0
            delay = backoff + jitter
            if backoff > RECONNECT_BASE_BACKOFF:
                log.info("Reconnecting in %.2fs (backoff)", delay)
                time.sleep(delay)

            log.info("Attempting connect to Gateway (backoff base %.1fs)...", backoff)
            sock = websocket.create_connection(GATEWAY_URL, timeout=10)
            ws = sock
            sock.settimeout(RECV_TIMEOUT)

            hello_raw = sock.recv()
            hello = json.loads(hello_raw)
            if hello.get("op") != OP_HELLO or "d" not in hello:
                sock.close()
                raise RuntimeError("Unexpected HELLO payload.")
            heartbeat_interval_ms = hello["d"]["heartbeat_interval"]

            hb_stop = threading.Event()
            hb_thread = threading.Thread(target=heartbeat_loop, args=(sock, heartbeat_interval_ms, hb_stop), daemon=True)
            hb_thread.start()

            if session_id and sequence is not None:
                resume_payload = {"op": OP_RESUME, "d": {"token": TOKEN, "session_id": session_id, "seq": sequence}}
                log.info("Attempting RESUME (session_id present).")
                send_json(sock, resume_payload)
            else:
                identify_payload = {
                    "op": OP_IDENTIFY,
                    "d": {
                        "token": TOKEN,
                        "properties": IDENTITY_PROPS,
                        "presence": {"status": STATUS, "afk": False},
                        "compress": False,
                        "intents": 0
                    }
                }
                log.info("Sending IDENTIFY (new session) with device=%s.", DEVICE_TYPE)
                send_json(sock, identify_payload)
                time.sleep(1)
                pres = build_presence_payload()
                send_json(sock, pres)

            backoff = RECONNECT_BASE_BACKOFF

            while not should_stop.is_set():
                try:
                    raw = sock.recv()
                    if not raw:
                        raise websocket.WebSocketConnectionClosedException("Received empty data.")
                    data = json.loads(raw)
                    op = data.get("op")
                    if op == OP_DISPATCH:
                        handle_dispatch(data)
                    elif op == OP_RECONNECT:
                        raise RuntimeError("Gateway requested reconnect (OP 7).")
                    elif op == OP_INVALID_SESSION:
                        resumable = data.get("d", False)
                        if not resumable:
                            session_id = None
                            sequence = None
                            log.warning("Non-resumable invalid session (OP 9). Resetting state.")
                            safe_save_state()
                        raise RuntimeError(f"Invalid session (OP 9): resumable={resumable}")
                    elif op == OP_HEARTBEAT_ACK:
                        last_ack_timestamp = time.time()
                        safe_save_state()
                    else:
                        log.debug("Received op %s", op)
                except websocket.WebSocketTimeoutException:
                    continue
                except (websocket.WebSocketConnectionClosedException, ConnectionResetError) as e:
                    raise RuntimeError(f"WebSocket closed: {e}")

        except Exception as exc:
            close_code = getattr(ws, "close_code", None) if ws else None
            if isinstance(exc, websocket.WebSocketException) and close_code:
                if close_code in RESET_SESSION_CODES:
                    session_id = None
                    sequence = None
                    log.error("Fatal gateway close code %s: resetting session.", close_code)
                else:
                    log.warning("Gateway closed with code %s.", close_code)

            if hb_thread:
                try:
                    hb_stop.set()
                    hb_thread.join(timeout=2)
                except Exception:
                    pass
            try:
                if ws:
                    ws.close()
            except Exception:
                pass

            err_text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            log.warning("Gateway error: %s. Will reconnect after backoff.", err_text)
            backoff = min(max_backoff, backoff * 2) if backoff > 0 else RECONNECT_BASE_BACKOFF
            continue

def main():
    log.info("Logged in as: %s#%s (%s). Identity device: %s", USERNAME, DISCRIMINATOR, USERID, DEVICE_TYPE)
    log.info("Config: STATUS=%s CUSTOM='%s' PERSIST=%s", STATUS, CUSTOM_STATUS, PERSIST_PATH)

    try:
        from keep_alive import keep_alive
        keep_alive()
    except Exception:
        log.exception("Failed to start keep_alive web server")

    gw_thread = threading.Thread(target=open_gateway_and_run, daemon=True)
    gw_thread.start()

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        should_stop.set()
        log.info("Shutting down by user request...")
        gw_thread.join(timeout=5)
        safe_save_state()
        sys.exit(0)

if __name__ == "__main__":
    main()
