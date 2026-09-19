import os
import re
import requests

from agent import run_agent
from memory import load_history, save_history
from langchain_core.messages import HumanMessage, AIMessage

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_DIGEST_CHANNEL = os.getenv("SLACK_DIGEST_CHANNEL")


def post_to_slack(channel: str, text: str) -> dict:
    if not SLACK_BOT_TOKEN:
        return {"status": "skipped", "reason": "SLACK_BOT_TOKEN not configured"}
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"channel": channel, "text": text},
        timeout=10,
    )
    return resp.json()


def handle_slack_event(payload: dict) -> dict:
    """Slack Events API webhook handler — URL verification handshake, then app_mention/DM -> agent -> reply.
    Signing-secret verification is left to the caller (main.py) so this stays testable without a live Slack app."""
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    event = payload.get("event") or {}
    if event.get("bot_id") or payload.get("type") != "event_callback":
        return {"ok": True}

    if event.get("type") not in ("app_mention", "message"):
        return {"ok": True}

    text = re.sub(r"<@[A-Z0-9]+>", "", event.get("text", "")).strip()
    channel = event.get("channel")
    user = event.get("user", "unknown")
    if not text or not channel:
        return {"ok": True}

    session_id = f"slack:{channel}:{user}"
    history = load_history(session_id)
    result = run_agent(text, history)
    save_history(session_id, history + [HumanMessage(content=text), AIMessage(content=result["answer"])])

    post_to_slack(channel, result["answer"])
    return {"ok": True}
