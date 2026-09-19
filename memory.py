import os
import json
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage

# In-memory fallback when Upstash Redis is not configured
_local: dict[str, list] = {}

_redis = None


def _get_redis():
    global _redis
    if _redis is not None:
        return _redis
    url = os.getenv("UPSTASH_REDIS_REST_URL")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
    if url and token:
        from upstash_redis import Redis
        _redis = Redis(url=url, token=token)
    return _redis


def load_history(session_id: str) -> list[BaseMessage]:
    r = _get_redis()
    if r:
        raw = r.get(f"session:{session_id}")
        data = json.loads(raw) if raw else []
    else:
        data = _local.get(session_id, [])

    return [
        HumanMessage(content=m["content"]) if m["role"] == "human"
        else AIMessage(content=m["content"])
        for m in data
    ]


def save_history(session_id: str, history: list[BaseMessage]):
    data = [
        {"role": "human" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
        for m in history
    ]
    r = _get_redis()
    if r:
        r.setex(f"session:{session_id}", 1800, json.dumps(data))
    else:
        _local[session_id] = data


def clear_history(session_id: str):
    r = _get_redis()
    if r:
        r.delete(f"session:{session_id}")
    else:
        _local.pop(session_id, None)


def ping() -> dict:
    r = _get_redis()
    if not r:
        return {"status": "not_configured"}
    try:
        r.get("__nexus_health__")
        return {"status": "up"}
    except Exception as e:
        return {"status": "down", "error": str(e)}
