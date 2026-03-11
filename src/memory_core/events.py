"""
事件发布模块 - 用于实时通知 Dashboard
"""
import json
from pathlib import Path
from datetime import datetime
from loguru import logger

EVENTS_FILE = Path(__file__).parent.parent.parent / "data" / "events.json"


def publish_event(event_type: str, message: str, details: str = ""):
    """发布事件到事件文件，Dashboard 会轮询读取"""
    try:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)

        # 读取现有事件
        events = []
        if EVENTS_FILE.exists():
            try:
                with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    events = data.get("events", [])
            except:
                events = []

        # 添加新事件
        now = datetime.now()
        events.append({
            "type": event_type,
            "message": message,
            "details": details,
            "timestamp": now.timestamp(),
            "time_str": now.strftime("%H:%M:%S"),
        })

        # 只保留最近 50 个事件
        events = events[-50:]

        # 写回文件
        with open(EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"events": events}, f, ensure_ascii=False, indent=2)

        logger.debug(f"Event published: [{event_type}] {message}")
    except Exception as e:
        logger.warning(f"Failed to publish event: {e}")


# 预定义的事件类型
EVENT_SUMMARY_START = "summary"
EVENT_SUMMARY_DONE = "summary_done"
EVENT_SUMMARY_EDITED = "summary_edited"
EVENT_KNOWLEDGE_START = "knowledge"
EVENT_KNOWLEDGE_DONE = "knowledge_done"
EVENT_KNOWLEDGE_EDITED = "knowledge_edited"
EVENT_DECISION_START = "decision"
EVENT_DECISION_DONE = "decision_done"
EVENT_DECISION_REGENERATED = "decision_regenerated"
EVENT_DECISION_BATCH_REGENERATED = "decision_batch_regenerated"
EVENT_DECISION_CONFIRMED = "decision_confirmed"
EVENT_DECISION_REJECTED = "decision_rejected"
EVENT_DECISION_EDITED = "decision_edited"
EVENT_DECISION_DELETED = "decision_deleted"
EVENT_EMBEDDING_START = "embedding"
EVENT_EMBEDDING_DONE = "embedding_done"
EVENT_SESSION_START = "session"
EVENT_SESSION_END = "session_end"
EVENT_ERROR = "error"
