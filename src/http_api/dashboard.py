#!/usr/bin/env python3
"""
Hybrid Memory Dashboard - Web UI
"""
import os
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from flask import Flask, request, Response
import json
from loguru import logger

# 配置 loguru 写入统一日志文件
MEMORY_BASE = Path(__file__).parent.parent.parent / "data"
LOG_FILE = MEMORY_BASE / "app.log"
MEMORY_BASE.mkdir(parents=True, exist_ok=True)

# 移除默认 handler，添加文件输出
logger.remove()
logger.add(
    LOG_FILE,
    rotation="10 MB",
    retention="1 hour",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
    level="DEBUG",
    encoding="utf-8",
)
# 同时输出到 stderr（终端）
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | <level>{message}</level>",
    level="INFO",
)
from src.memory_core import (
    MemoryManager, ConfigManager, DEFAULT_CONFIG, CONFIG_META,
    EXTRACTION_PROMPT, SUMMARY_PROMPT, SUMMARY_PROMPT_WITH_CONTEXT,
    ContentConfig, process_content
)
from src.memory_core.database import Database

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 支持中文

# MEMORY_BASE 和 LOG_FILE 已在顶部定义
GLOBAL_DB = MEMORY_BASE / "global_memory.db"
PROJECTS_DIR = MEMORY_BASE / "projects"


def json_response(data):
    """返回正确编码的 JSON 响应"""
    return Response(
        json.dumps(data, ensure_ascii=False, indent=2),
        mimetype='application/json; charset=utf-8'
    )


def get_project_list():
    if not PROJECTS_DIR.exists():
        return []
    return [f.stem for f in PROJECTS_DIR.glob("*.db")]


# ============ API ============

@app.route("/api/projects")
def list_projects():
    projects = get_project_list()
    result = []

    global_db = Database(GLOBAL_DB) if GLOBAL_DB.exists() else None
    input_price = float(global_db.get_config("input_token_price", "0.003")) if global_db else 0.003
    output_price = float(global_db.get_config("output_token_price", "0.015")) if global_db else 0.015

    total_input_tokens = 0
    total_output_tokens = 0

    for p in projects:
        db_path = PROJECTS_DIR / f"{p}.db"
        try:
            db = Database(db_path)
            with db._connect() as conn:
                msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                summary_count = conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
                try:
                    token_row = conn.execute(
                        "SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0) FROM token_usage"
                    ).fetchone()
                    input_tokens = token_row[0]
                    output_tokens = token_row[1]
                except Exception:
                    input_tokens = 0
                    output_tokens = 0

            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            input_cost = (input_tokens / 1000) * input_price
            output_cost = (output_tokens / 1000) * output_price

            result.append({
                "name": p,
                "messages": msg_count,
                "sessions": session_count,
                "summaries": summary_count,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": round(input_cost + output_cost, 4),
            })
        except Exception as e:
            result.append({"name": p, "error": str(e)})

    total_input_cost = (total_input_tokens / 1000) * input_price
    total_output_cost = (total_output_tokens / 1000) * output_price

    return json_response({
        "projects": result,
        "global_db": str(GLOBAL_DB),
        "totals": {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "input_cost": round(total_input_cost, 4),
            "output_cost": round(total_output_cost, 4),
            "total_cost": round(total_input_cost + total_output_cost, 4),
            "input_price_per_1k": input_price,
            "output_price_per_1k": output_price,
        }
    })


@app.route("/api/projects/<project_name>/sessions")
def get_sessions(project_name):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    with db._connect() as conn:
        # 获取每个 session 的消息数量
        rows = conn.execute(
            """SELECT s.session_id, s.started_at, s.last_active_at, s.is_active,
               (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.session_id) as msg_count
               FROM sessions s ORDER BY s.last_active_at DESC"""
        ).fetchall()
    return json_response({
        "sessions": [{"session_id": r[0], "started_at": str(r[1]), "last_active_at": str(r[2]), "is_active": bool(r[3]), "message_count": r[4]} for r in rows]
    })


@app.route("/api/projects/<project_name>/sessions/cleanup", methods=["POST"])
def cleanup_empty_sessions(project_name):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    deleted = db.delete_empty_sessions()
    return json_response({"deleted": deleted, "message": f"Deleted {deleted} empty sessions"})


@app.route("/api/projects/<project_name>/messages")
def get_messages(project_name):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    session_id = request.args.get("session_id")
    limit = int(request.args.get("limit", 100))
    db = Database(db_path)
    with db._connect() as conn:
        if session_id:
            rows = conn.execute(
                "SELECT id, session_id, role, content, timestamp, is_summarized, COALESCE(model, '') as model FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, session_id, role, content, timestamp, is_summarized, COALESCE(model, '') as model FROM messages ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return json_response({
        "messages": [{"id": r[0], "session_id": r[1], "role": r[2], "content": r[3], "timestamp": str(r[4]), "is_summarized": bool(r[5]), "model": r[6]} for r in rows]
    })


@app.route("/api/projects/<project_name>/messages/range")
def get_messages_range(project_name):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    start = int(request.args.get("start", 0))
    end = int(request.args.get("end", 0))
    db = Database(db_path)
    messages = db.get_messages_in_range(start, end)

    # 加载内容处理配置
    from src.memory_core.config import load_config
    config_mgr = load_config(GLOBAL_DB)
    content_config = ContentConfig(
        include_thinking=config_mgr.get("content_include_thinking").lower() == "true",
        include_tool=config_mgr.get("content_include_tool").lower() == "true",
        include_text=config_mgr.get("content_include_text").lower() == "true",
        max_chars_thinking=config_mgr.get_int("content_max_chars_thinking"),
        max_chars_tool=config_mgr.get_int("content_max_chars_tool"),
        max_chars_text=config_mgr.get_int("content_max_chars_text"),
    )

    result_messages = []
    for m in messages:
        processed = process_content(m.content, content_config)
        result_messages.append({
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "processed_content": processed,
            "timestamp": str(m.timestamp),
            "model": m.model
        })

    return json_response({"messages": result_messages})


@app.route("/api/projects/<project_name>/summaries")
def get_summaries(project_name):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT id, session_id, summary_text, message_count, message_range_start, message_range_end, created_at FROM summaries ORDER BY id DESC"
        ).fetchall()
    return json_response({
        "summaries": [{"id": r[0], "session_id": r[1], "summary_text": r[2], "message_count": r[3], "message_range_start": r[4], "message_range_end": r[5], "created_at": str(r[6])} for r in rows]
    })


@app.route("/api/projects/<project_name>/interactions")
def get_interactions(project_name):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    session_id = request.args.get("session_id")
    limit = int(request.args.get("limit", 100))
    db = Database(db_path)
    with db._connect() as conn:
        if session_id:
            rows = conn.execute(
                """SELECT id, session_id, type, tool_name, request_content, options, user_response, timestamp
                   FROM interactions WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, session_id, type, tool_name, request_content, options, user_response, timestamp
                   FROM interactions ORDER BY timestamp DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    return json_response({
        "interactions": [{
            "id": r[0], "session_id": r[1], "type": r[2], "tool_name": r[3],
            "request_content": r[4], "options": r[5], "user_response": r[6], "timestamp": str(r[7])
        } for r in rows]
    })


@app.route("/api/projects/<project_name>/summaries/<int:summary_id>", methods=["PUT"])
def update_summary(project_name, summary_id):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    data = request.json
    if not data or "summary_text" not in data:
        return json_response({"error": "summary_text required"}), 400
    db = Database(db_path)
    db.update_summary_text(summary_id, data["summary_text"])
    return json_response({"status": "ok"})


@app.route("/api/projects/<project_name>/summaries/<int:summary_id>/regenerate", methods=["POST"])
def regenerate_summary(project_name, summary_id):
    from src.memory_core.events import publish_event
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    summary = db.get_summary_by_id(summary_id)
    if not summary:
        return json_response({"error": "Summary not found"}), 404
    if not summary.message_range_start or not summary.message_range_end:
        return json_response({"error": "Summary has no message range"}), 400
    messages = db.get_messages_in_range(summary.message_range_start, summary.message_range_end)
    if not messages:
        return json_response({"error": "No messages found in range"}), 400

    publish_event("summary", f"Regenerating summary #{summary_id} ({len(messages)} messages)", project_name)

    from src.memory_core.config import load_config
    from src.memory_core.summarizer import SummaryGenerator
    from src.memory_core.llm_client import create_llm_client
    config_mgr = load_config(GLOBAL_DB)
    custom_template = config_mgr.get("summary_prompt_template")
    llm_client = create_llm_client(
        provider=config_mgr.get("llm_provider"),
        ollama_model=config_mgr.get("ollama_model"),
        ollama_base_url=config_mgr.get("ollama_base_url"),
        ollama_timeout=float(config_mgr.get("ollama_timeout")),
        ollama_keep_alive=config_mgr.get("ollama_keep_alive"),
    )
    generator = SummaryGenerator(llm_client)
    all_summaries = db.get_all_summaries(limit=100)
    earlier = [s for s in all_summaries if s.id < summary_id]
    previous_context = "\n\n---\n\n".join(s.summary_text for s in earlier[:3]) if earlier else ""
    new_text = generator.generate(messages, previous_context, custom_template)
    db.update_summary_text(summary_id, new_text)

    publish_event("summary_done", f"Summary #{summary_id} regenerated", project_name)

    return json_response({"status": "ok", "summary_text": new_text})


@app.route("/api/projects/<project_name>/summaries/regenerate-all", methods=["POST"])
def regenerate_all_summaries(project_name):
    from src.memory_core.events import publish_event
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    from src.memory_core.config import load_config
    from src.memory_core.summarizer import SummaryGenerator
    from src.memory_core.llm_client import create_llm_client
    config_mgr = load_config(GLOBAL_DB)
    custom_template = config_mgr.get("summary_prompt_template")
    llm_client = create_llm_client(
        provider=config_mgr.get("llm_provider"),
        ollama_model=config_mgr.get("ollama_model"),
        ollama_base_url=config_mgr.get("ollama_base_url"),
        ollama_timeout=float(config_mgr.get("ollama_timeout")),
        ollama_keep_alive=config_mgr.get("ollama_keep_alive"),
    )
    generator = SummaryGenerator(llm_client)
    all_summaries = db.get_all_summaries(limit=100)
    all_summaries.sort(key=lambda s: s.id)

    publish_event("summary", f"Regenerating all {len(all_summaries)} summaries", project_name)

    regenerated = 0
    for i, summary in enumerate(all_summaries):
        if not summary.message_range_start or not summary.message_range_end:
            continue
        messages = db.get_messages_in_range(summary.message_range_start, summary.message_range_end)
        if not messages:
            continue
        publish_event("summary", f"Regenerating summary {i+1}/{len(all_summaries)}", project_name)
        previous_context = "\n\n---\n\n".join(s.summary_text for s in all_summaries[:i][-3:]) if i > 0 else ""
        new_text = generator.generate(messages, previous_context, custom_template)
        db.update_summary_text(summary.id, new_text)
        regenerated += 1

    publish_event("summary_done", f"All summaries regenerated ({regenerated} total)", project_name)

    return json_response({"status": "ok", "regenerated": regenerated})


@app.route("/api/projects/<project_name>/summaries/trigger", methods=["POST"])
def trigger_summary(project_name):
    """手动触发总结（不管是否达到阈值）"""
    from src.memory_core.config import load_config
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404

    db = Database(db_path)
    config_mgr = load_config(GLOBAL_DB)
    config_kwargs = config_mgr.get_memory_manager_kwargs()

    manager = MemoryManager(db_path=db_path, **config_kwargs)

    # 获取最新 session
    with db._connect() as conn:
        row = conn.execute("SELECT session_id FROM sessions ORDER BY last_active_at DESC LIMIT 1").fetchone()
    session_id = row[0] if row else f"{project_name}-session"

    # 检查是否有未总结的消息
    unsummarized = db.count_unsummarized_messages(session_id=None)
    if unsummarized == 0:
        return json_response({"message": "No unsummarized messages", "created": False})

    publish_event("summary", f"Manually triggering summary ({unsummarized} messages)", project_name)

    # 触发总结
    summary = manager.trigger_summary(session_id)
    if summary:
        publish_event("summary_done", f"Summary #{summary.id} created", project_name)
        return json_response({
            "message": f"Summary created",
            "created": True,
            "summary_id": summary.id,
            "message_count": summary.message_count
        })
    else:
        return json_response({"message": "Summary generation failed", "created": False})


@app.route("/api/projects/<project_name>/summaries/selection", methods=["GET"])
def get_summary_selection(project_name):
    db = Database(GLOBAL_DB)
    config_mgr = ConfigManager(db)
    selected_config = config_mgr.get("selected_summary_ids")
    try:
        selected_map = json.loads(selected_config) if selected_config else {}
    except json.JSONDecodeError:
        selected_map = {}
    return json_response({"selected_ids": selected_map.get(project_name, [])})


@app.route("/api/projects/<project_name>/summaries/selection", methods=["POST"])
def set_summary_selection(project_name):
    data = request.json
    if not data or "selected_ids" not in data:
        return json_response({"error": "selected_ids required"}), 400
    db = Database(GLOBAL_DB)
    config_mgr = ConfigManager(db)
    selected_config = config_mgr.get("selected_summary_ids")
    try:
        selected_map = json.loads(selected_config) if selected_config else {}
    except json.JSONDecodeError:
        selected_map = {}
    selected_map[project_name] = data["selected_ids"]
    config_mgr.set("selected_summary_ids", json.dumps(selected_map))
    return json_response({"status": "ok"})


@app.route("/api/projects/<project_name>/decisions/selection", methods=["GET"])
def get_decision_selection(project_name):
    db = Database(GLOBAL_DB)
    config_mgr = ConfigManager(db)
    selected_config = config_mgr.get("selected_decision_ids")
    try:
        selected_map = json.loads(selected_config) if selected_config else {}
    except json.JSONDecodeError:
        selected_map = {}
    return json_response({"selected_ids": selected_map.get(project_name, [])})


@app.route("/api/projects/<project_name>/decisions/selection", methods=["POST"])
def set_decision_selection(project_name):
    data = request.json
    if not data or "selected_ids" not in data:
        return json_response({"error": "selected_ids required"}), 400
    db = Database(GLOBAL_DB)
    config_mgr = ConfigManager(db)
    selected_config = config_mgr.get("selected_decision_ids")
    try:
        selected_map = json.loads(selected_config) if selected_config else {}
    except json.JSONDecodeError:
        selected_map = {}
    selected_map[project_name] = data["selected_ids"]
    config_mgr.set("selected_decision_ids", json.dumps(selected_map))
    return json_response({"status": "ok"})


@app.route("/api/projects/<project_name>/context")
def get_context(project_name):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)

    # 从配置读取注入参数（与 sessionStart.py 完全一致）
    from src.memory_core.config import load_config
    config_mgr = load_config(GLOBAL_DB)
    inject_summary_count = config_mgr.get_int("inject_summary_count")
    inject_recent_count = config_mgr.get_int("inject_recent_count")
    inject_knowledge_count = config_mgr.get_int("inject_knowledge_count")
    inject_task_count = config_mgr.get_int("inject_task_count")
    inject_decision_count = config_mgr.get_int("inject_decision_count")

    # 与 sessionStart.py 完全一致：最新 N 条 + 额外选中的
    auto_summaries = db.get_all_summaries(limit=inject_summary_count)
    auto_ids = {s.id for s in auto_summaries}

    selected_config = config_mgr.get("selected_summary_ids")
    try:
        selected_map = json.loads(selected_config) if selected_config else {}
    except json.JSONDecodeError:
        selected_map = {}
    extra_ids = selected_map.get(project_name, [])

    extra_summaries = []
    for sid in extra_ids:
        if sid not in auto_ids:
            s = db.get_summary_by_id(sid)
            if s:
                extra_summaries.append(s)

    # 顺序：extra（手动选的背景）在上，auto（最新的）在下
    # auto_summaries 是 DESC 排序（最新在前），需要反转为旧到新
    summaries = extra_summaries + list(reversed(auto_summaries))

    recent_messages = db.get_recent_messages_all_sessions(limit=inject_recent_count)
    raw_knowledge = db.get_knowledge(None)

    # 与 sessionStart.py 完全一致：注入全部 6 类，并应用数量限制
    knowledge = {
        "user_preferences": raw_knowledge.get("user_preferences", [])[:inject_knowledge_count],
        "project_decisions": raw_knowledge.get("project_decisions", [])[:inject_knowledge_count],
        "key_facts": raw_knowledge.get("key_facts", [])[:inject_knowledge_count],
        "pending_tasks": raw_knowledge.get("pending_tasks", [])[:inject_task_count],
        "learned_patterns": raw_knowledge.get("learned_patterns", [])[:inject_knowledge_count],
        "important_context": raw_knowledge.get("important_context", [])[:inject_knowledge_count],
    }

    # 使用 content_processor 处理消息内容（与 sessionStart.py 完全一致）
    content_config = ContentConfig(
        include_thinking=config_mgr.get("content_include_thinking").lower() == "true",
        include_tool=config_mgr.get("content_include_tool").lower() == "true",
        include_text=config_mgr.get("content_include_text").lower() == "true",
        max_chars_thinking=config_mgr.get_int("content_max_chars_thinking"),
        max_chars_tool=config_mgr.get_int("content_max_chars_tool"),
        max_chars_text=config_mgr.get_int("content_max_chars_text"),
    )

    processed_messages = []
    for m in recent_messages or []:  # recent_messages 已经是旧到新排序
        processed = process_content(m.content, content_config)
        if not processed:
            continue  # 用户关闭了所有类型，跳过此消息
        processed_messages.append({"role": m.role, "content": processed})

    # 获取已确认的决策（与 summaries 一致：auto + extra）
    auto_decisions = db.get_decisions(project=project_name, status="confirmed", limit=inject_decision_count)
    auto_decision_ids = {d.id for d in auto_decisions}

    selected_decision_config = config_mgr.get("selected_decision_ids")
    try:
        selected_decision_map = json.loads(selected_decision_config) if selected_decision_config else {}
    except json.JSONDecodeError:
        selected_decision_map = {}
    extra_decision_ids = selected_decision_map.get(project_name, [])

    extra_decisions = []
    for did in extra_decision_ids:
        if did not in auto_decision_ids:
            d = db.get_decision_by_id(did)
            if d and d.status == "confirmed":
                extra_decisions.append(d)

    # 顺序：extra 在上，auto 在下（auto 是 DESC 排序，需要反转）
    all_decisions = extra_decisions + list(reversed(auto_decisions))
    decision_list = [{
        "id": d.id,
        "problem": d.problem,
        "solution": d.solution,
        "reason": d.reason
    } for d in all_decisions]

    context = {
        "summaries": "\n\n---\n\n".join(s.summary_text for s in summaries) if summaries else "",
        "messages": processed_messages,
        "knowledge": knowledge,
        "decisions": decision_list
    }
    return json_response(context)


@app.route("/api/global/search")
def search_global():
    query = request.args.get("query", "")
    fuzzy = request.args.get("fuzzy", "false").lower() == "true"
    threshold = int(request.args.get("threshold", "60"))
    if not query or not GLOBAL_DB.exists():
        return json_response({"results": []})
    manager = MemoryManager(db_path=GLOBAL_DB)
    results = manager.search_memory(query, fuzzy=fuzzy, threshold=threshold)
    return json_response({
        "results": [{"id": m.id, "session_id": m.session_id, "role": m.role, "content": m.content, "timestamp": str(m.timestamp)} for m in results[:50]]
    })


@app.route("/api/projects/<project_name>/vector-search")
def vector_search(project_name):
    """向量语义搜索"""
    query = request.args.get("query", "")
    k = int(request.args.get("k", "10"))
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    if not query:
        return json_response({"results": []})
    try:
        from src.memory_core.config import load_config
        config_mgr = load_config(GLOBAL_DB)
        config_kwargs = config_mgr.get_memory_manager_kwargs()
        manager = MemoryManager(db_path=db_path, **config_kwargs)
        preview_len = config_mgr.get_int("search_result_preview_length")
        results = manager.vector_search(query, k=k)
        return json_response({
            "results": [{"id": m.id, "role": m.role, "content": m.content[:preview_len] if len(m.content) > preview_len else m.content, "score": round(score, 4), "timestamp": str(m.timestamp)} for m, score in results]
        })
    except Exception as e:
        return json_response({"error": str(e)}), 500


@app.route("/api/search")
def unified_search():
    """统一搜索 API - 支持多种搜索方式和范围"""
    query = request.args.get("query", "")
    method = request.args.get("method", "combined")  # vector, bm25, fuzzy, combined
    scope = request.args.get("scope", "current")  # current, all
    project = request.args.get("project", "")
    limit = int(request.args.get("limit", "30"))
    threshold = int(request.args.get("threshold", "60"))

    if not query:
        return json_response({"results": [], "method": method, "scope": scope})

    from src.memory_core.config import load_config

    results = []

    def search_project(db_path, proj_name):
        try:
            config_mgr = load_config(GLOBAL_DB)
            config_kwargs = config_mgr.get_memory_manager_kwargs()
            manager = MemoryManager(db_path=db_path, **config_kwargs)

            proj_results = []

            if method == "vector":
                for msg, score in manager.vector_search(query, k=limit):
                    proj_results.append({
                        "id": msg.id, "project": proj_name, "role": msg.role,
                        "content": msg.content, "score": round(score, 4),
                        "timestamp": str(msg.timestamp), "method": "vector", "model": msg.model
                    })
            elif method == "bm25":
                for msg, score in manager.bm25_search(query, limit=limit):
                    proj_results.append({
                        "id": msg.id, "project": proj_name, "role": msg.role,
                        "content": msg.content, "score": round(score, 4),
                        "timestamp": str(msg.timestamp), "method": "bm25", "model": msg.model
                    })
            elif method == "fuzzy":
                for msg in manager.search_memory(query, fuzzy=True, threshold=threshold):
                    proj_results.append({
                        "id": msg.id, "project": proj_name, "role": msg.role,
                        "content": msg.content, "score": 0,
                        "timestamp": str(msg.timestamp), "method": "fuzzy", "model": msg.model
                    })
            else:  # combined - 真正的加权融合
                # 分别获取两种搜索结果
                vector_results = manager.vector_search(query, k=limit)
                bm25_results = manager.bm25_search(query, limit=limit)

                # 归一化分数 (0-1)
                def normalize_scores(results):
                    if not results:
                        return {}
                    scores = [s for _, s in results]
                    max_s, min_s = max(scores) if scores else 1, min(scores) if scores else 0
                    range_s = max_s - min_s if max_s != min_s else 1
                    return {msg.id: (score - min_s) / range_s for msg, score in results}

                vector_scores = normalize_scores(vector_results)
                bm25_scores = normalize_scores(bm25_results)

                # 合并所有消息
                all_msgs = {}
                for msg, _ in vector_results:
                    all_msgs[msg.id] = msg
                for msg, _ in bm25_results:
                    all_msgs[msg.id] = msg

                # 加权融合 (vector: 0.5, bm25: 0.5)
                VECTOR_WEIGHT = 0.5
                BM25_WEIGHT = 0.5
                combined_scores = {}
                for msg_id, msg in all_msgs.items():
                    v_score = vector_scores.get(msg_id, 0)
                    b_score = bm25_scores.get(msg_id, 0)
                    combined_scores[msg_id] = v_score * VECTOR_WEIGHT + b_score * BM25_WEIGHT

                # 按融合分数排序
                sorted_ids = sorted(combined_scores.keys(), key=lambda x: combined_scores[x], reverse=True)

                for msg_id in sorted_ids[:limit]:
                    msg = all_msgs[msg_id]
                    v_s = vector_scores.get(msg_id, 0)
                    b_s = bm25_scores.get(msg_id, 0)
                    # 标注主要来源
                    source = "vector+bm25" if v_s > 0 and b_s > 0 else ("vector" if v_s > 0 else "bm25")
                    proj_results.append({
                        "id": msg.id, "project": proj_name, "role": msg.role,
                        "content": msg.content, "score": round(combined_scores[msg_id], 4),
                        "timestamp": str(msg.timestamp), "method": source,
                        "vector_score": round(v_s, 3), "bm25_score": round(b_s, 3), "model": msg.model
                    })

            return proj_results
        except Exception as e:
            return [{"error": str(e), "project": proj_name}]

    if scope == "all":
        # 搜索所有项目
        for proj_name in get_project_list():
            db_path = PROJECTS_DIR / f"{proj_name}.db"
            results.extend(search_project(db_path, proj_name))
    else:
        # 只搜索当前项目
        if not project:
            return json_response({"error": "Project required for current scope", "results": []})
        db_path = PROJECTS_DIR / f"{project}.db"
        if not db_path.exists():
            return json_response({"error": "Project not found", "results": []})
        results = search_project(db_path, project)

    # 按分数排序（combined 模式下混合排序）
    results = [r for r in results if "error" not in r]
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    results = results[:limit]

    return json_response({"results": results, "method": method, "scope": scope, "total": len(results)})


@app.route("/api/search/decisions")
def unified_search_decisions():
    """Decision 统一搜索 API - 支持 vector, bm25, fuzzy, combined"""
    query = request.args.get("query", "")
    method = request.args.get("method", "combined")
    scope = request.args.get("scope", "current")
    project = request.args.get("project", "")
    limit = int(request.args.get("limit", "30"))
    threshold = int(request.args.get("threshold", "60"))

    if not query:
        return json_response({"results": [], "method": method, "scope": scope})

    from src.memory_core.config import load_config

    results = []

    def search_project_decisions(db_path, proj_name):
        try:
            config_mgr = load_config(GLOBAL_DB)
            config_kwargs = config_mgr.get_memory_manager_kwargs()
            manager = MemoryManager(db_path=db_path, **config_kwargs)

            proj_results = []

            if method == "vector":
                for decision, score in manager.decision_vector_search(query, k=limit):
                    proj_results.append({
                        "id": decision.id, "project": proj_name,
                        "problem": decision.problem, "solution": decision.solution,
                        "reason": decision.reason or "", "note": decision.note or "",
                        "score": round(score, 4), "timestamp": str(decision.timestamp),
                        "method": "vector"
                    })
            elif method == "bm25":
                for decision, score in manager.decision_bm25_search(query, limit=limit):
                    proj_results.append({
                        "id": decision.id, "project": proj_name,
                        "problem": decision.problem, "solution": decision.solution,
                        "reason": decision.reason or "", "note": decision.note or "",
                        "score": round(score, 4), "timestamp": str(decision.timestamp),
                        "method": "bm25"
                    })
            elif method == "fuzzy":
                for decision, score in manager.retriever.decision_fuzzy_search(query, limit=limit, threshold=threshold):
                    proj_results.append({
                        "id": decision.id, "project": proj_name,
                        "problem": decision.problem, "solution": decision.solution,
                        "reason": decision.reason or "", "note": decision.note or "",
                        "score": round(score, 4), "timestamp": str(decision.timestamp),
                        "method": "fuzzy"
                    })
            else:  # combined
                vector_results = manager.decision_vector_search(query, k=limit)
                bm25_results = manager.decision_bm25_search(query, limit=limit)

                def normalize_scores(results):
                    if not results:
                        return {}
                    scores = [s for _, s in results]
                    max_s, min_s = max(scores) if scores else 1, min(scores) if scores else 0
                    range_s = max_s - min_s if max_s != min_s else 1
                    return {d.id: (score - min_s) / range_s for d, score in results}

                vector_scores = normalize_scores(vector_results)
                bm25_scores = normalize_scores(bm25_results)

                all_decisions = {}
                for d, _ in vector_results:
                    all_decisions[d.id] = d
                for d, _ in bm25_results:
                    all_decisions[d.id] = d

                VECTOR_WEIGHT = 0.5
                BM25_WEIGHT = 0.5
                combined_scores = {}
                for d_id, d in all_decisions.items():
                    v_score = vector_scores.get(d_id, 0)
                    b_score = bm25_scores.get(d_id, 0)
                    combined_scores[d_id] = v_score * VECTOR_WEIGHT + b_score * BM25_WEIGHT

                sorted_ids = sorted(combined_scores.keys(), key=lambda x: combined_scores[x], reverse=True)

                for d_id in sorted_ids[:limit]:
                    d = all_decisions[d_id]
                    v_s = vector_scores.get(d_id, 0)
                    b_s = bm25_scores.get(d_id, 0)
                    source = "vector+bm25" if v_s > 0 and b_s > 0 else ("vector" if v_s > 0 else "bm25")
                    proj_results.append({
                        "id": d.id, "project": proj_name,
                        "problem": d.problem, "solution": d.solution,
                        "reason": d.reason or "", "note": d.note or "",
                        "score": round(combined_scores[d_id], 4),
                        "timestamp": str(d.timestamp), "method": source,
                        "vector_score": round(v_s, 3), "bm25_score": round(b_s, 3)
                    })

            return proj_results
        except Exception as e:
            return [{"error": str(e), "project": proj_name}]

    if scope == "all":
        for proj_name in get_project_list():
            db_path = PROJECTS_DIR / f"{proj_name}.db"
            results.extend(search_project_decisions(db_path, proj_name))
    else:
        if not project:
            return json_response({"error": "Project required for current scope", "results": []})
        db_path = PROJECTS_DIR / f"{project}.db"
        if not db_path.exists():
            return json_response({"error": "Project not found", "results": []})
        results = search_project_decisions(db_path, project)

    results = [r for r in results if "error" not in r]
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    results = results[:limit]

    return json_response({"results": results, "method": method, "scope": scope, "total": len(results)})


@app.route("/api/projects/<project_name>/knowledge")
def get_knowledge(project_name):
    """获取项目的结构化知识"""
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    knowledge = db.get_knowledge()

    # 读取配置的限制
    from src.memory_core.config import load_config
    config_mgr = load_config(GLOBAL_DB)
    max_per_category = config_mgr.get_int("knowledge_max_items_per_category")

    # 获取 pending 信息
    latest_msg_id = db.get_latest_message_id()
    last_knowledge_id = db.get_last_knowledge_message_id()
    pending_count = db.count_pending_knowledge_messages()

    return json_response({
        "knowledge": knowledge,
        "max_per_category": max_per_category,
        "latest_message_id": latest_msg_id,
        "last_knowledge_end_id": last_knowledge_id,
        "pending_count": pending_count,
    })


@app.route("/api/projects/<project_name>/knowledge/history")
def get_knowledge_history(project_name):
    """获取知识提取历史"""
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    history = db.get_knowledge_history(limit=50)
    return json_response({"history": history})


@app.route("/api/projects/<project_name>/knowledge/history/<int:history_id>", methods=["PUT"])
def update_knowledge_history(project_name, history_id):
    """更新知识历史记录"""
    import json
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    data = request.get_json()
    content = data.get("content", {})
    with db._connect() as conn:
        conn.execute(
            "UPDATE knowledge_history SET content = ? WHERE id = ?",
            (json.dumps(content, ensure_ascii=False), history_id)
        )
    return json_response({"status": "ok"})


@app.route("/api/projects/<project_name>/knowledge/extract", methods=["POST"])
def extract_knowledge(project_name):
    """手动触发知识提取：从最近的未总结消息中提取知识"""
    from src.memory_core.events import publish_event

    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)

    from src.memory_core.config import load_config
    from src.memory_core.knowledge_extractor import KnowledgeExtractor
    from src.memory_core.llm_client import create_llm_client

    config_mgr = load_config(GLOBAL_DB)

    # 获取未进行知识提取的消息
    messages = db.get_messages_for_knowledge()
    if not messages:
        return json_response({"status": "ok", "message": "No messages to extract from", "total": 0})

    publish_event("knowledge", f"Extracting knowledge from {len(messages)} messages", project_name)

    # 创建 LLM 客户端
    llm_client = create_llm_client(
        provider=config_mgr.get("llm_provider"),
        ollama_model=config_mgr.get("ollama_model"),
        ollama_base_url=config_mgr.get("ollama_base_url"),
        ollama_timeout=float(config_mgr.get("ollama_timeout")),
        ollama_keep_alive=config_mgr.get("ollama_keep_alive"),
    )

    content_config = ContentConfig(
        include_thinking=config_mgr.get("content_include_thinking").lower() == "true",
        include_tool=config_mgr.get("content_include_tool").lower() == "true",
        include_text=config_mgr.get("content_include_text").lower() == "true",
        max_chars_thinking=config_mgr.get_int("content_max_chars_thinking"),
        max_chars_tool=config_mgr.get_int("content_max_chars_tool"),
        max_chars_text=config_mgr.get_int("content_max_chars_text"),
    )
    max_items = config_mgr.get_int("knowledge_max_items_per_category")
    extractor = KnowledgeExtractor(llm_client, content_config=content_config, max_items_per_category=max_items)

    # 获取已有知识
    existing = db.get_knowledge()

    # 提取并融合知识（LLM 直接输出融合后的完整知识库）
    knowledge = extractor.extract(messages, existing)

    # 获取消息 ID 列表
    message_ids = [m.id for m in messages if m.id]

    # 保存知识历史版本（含消息范围）
    db.save_knowledge_history(None, knowledge, message_ids)

    # 直接保存（覆盖旧知识）
    with db._connect() as conn:
        conn.execute("DELETE FROM knowledge")
    db.save_knowledge(None, knowledge)

    # 标记消息为已知识提取
    if message_ids:
        db.mark_messages_knowledge_extracted(message_ids)

    total_items = sum(len(v) for v in knowledge.values())
    publish_event("knowledge_done", f"Knowledge updated: {total_items} items", project_name)

    return json_response({"status": "ok", "total": total_items})


@app.route("/api/projects/<project_name>/knowledge-debug")
def get_knowledge_debug(project_name):
    """获取知识提取的 prompt 和输入数据（用于调试）"""
    from src.memory_core.prompts import EXTRACTION_PROMPT
    from src.memory_core.config import load_config
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    config_mgr = load_config(GLOBAL_DB)
    content_config = ContentConfig(
        include_thinking=config_mgr.get("content_include_thinking").lower() == "true",
        include_tool=config_mgr.get("content_include_tool").lower() == "true",
        include_text=config_mgr.get("content_include_text").lower() == "true",
        max_chars_thinking=config_mgr.get_int("content_max_chars_thinking"),
        max_chars_tool=config_mgr.get_int("content_max_chars_tool"),
        max_chars_text=config_mgr.get_int("content_max_chars_text"),
    )

    # 获取未进行知识提取的消息
    messages = db.get_messages_for_knowledge()
    source = "pending_knowledge"

    # 格式化对话（使用 content_processor，与 KnowledgeExtractor 一致）
    from src.memory_core.prompts import ROLE_LABELS, CATEGORY_NAMES, UI_TEXT
    lines = []
    for msg in messages:
        role_label = ROLE_LABELS.get(msg.role, msg.role)
        content = process_content(msg.content, content_config)
        if not content:
            continue  # 用户关闭了所有类型，跳过此消息
        lines.append(f"{role_label}: {content}")
    conversation = "\n".join(lines)

    # 获取已有知识并格式化
    existing_knowledge = db.get_knowledge()
    no_knowledge_text = UI_TEXT.get("no_existing_knowledge", "(No existing knowledge)")
    if existing_knowledge:
        knowledge_lines = []
        for key, items in existing_knowledge.items():
            if items:
                name = CATEGORY_NAMES.get(key, key)
                knowledge_lines.append(f"- {name}: {', '.join(items[:10])}")
        existing_str = "\n".join(knowledge_lines) if knowledge_lines else no_knowledge_text
    else:
        existing_str = no_knowledge_text

    # 生成完整 prompt
    max_items = config_mgr.get_int("knowledge_max_items_per_category")
    prompt = EXTRACTION_PROMPT.format(conversation=conversation, existing_knowledge=existing_str, max_items=max_items)

    # 获取 pending 信息
    latest_msg_id = db.get_latest_message_id()
    last_knowledge_id = db.get_last_knowledge_message_id()
    pending_count = db.count_pending_knowledge_messages()

    return json_response({
        "message_count": len(messages),
        "message_source": source,
        "latest_message_id": latest_msg_id,
        "last_knowledge_end_id": last_knowledge_id,
        "pending_count": pending_count,
        "content_config": {
            "include_thinking": content_config.include_thinking,
            "include_tool": content_config.include_tool,
            "include_text": content_config.include_text,
            "max_chars_thinking": content_config.max_chars_thinking,
            "max_chars_tool": content_config.max_chars_tool,
            "max_chars_text": content_config.max_chars_text,
        },
        "messages": [{"id": m.id, "role": m.role, "content": c} for m in messages if (c := process_content(m.content, content_config))],
        "formatted_conversation": conversation,
        "existing_knowledge": existing_str,
        "full_prompt": prompt,
    })


@app.route("/api/projects/<project_name>/summary-debug")
def get_summary_debug(project_name):
    """获取即将发送给 summary 模型的内容（用于调试）- 与实际 summarizer 逻辑完全一致"""
    from src.memory_core.prompts import SUMMARY_PROMPT, SUMMARY_PROMPT_WITH_CONTEXT
    from src.memory_core.config import load_config
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    config_mgr = load_config(GLOBAL_DB)
    custom_template = config_mgr.get("summary_prompt_template")
    # 读取配置的截断参数
    max_chars_total = config_mgr.get_int("summary_max_chars_total")
    content_config = ContentConfig(
        include_thinking=config_mgr.get("content_include_thinking").lower() == "true",
        include_tool=config_mgr.get("content_include_tool").lower() == "true",
        include_text=config_mgr.get("content_include_text").lower() == "true",
        max_chars_thinking=config_mgr.get_int("content_max_chars_thinking"),
        max_chars_tool=config_mgr.get_int("content_max_chars_tool"),
        max_chars_text=config_mgr.get_int("content_max_chars_text"),
    )

    with db._connect() as conn:
        row = conn.execute("SELECT session_id FROM sessions ORDER BY last_active_at DESC LIMIT 1").fetchone()
    session_id = row[0] if row else f"{project_name}-session"
    # 获取所有未总结消息（跨 session），而不只是当前 session
    messages = db.get_unsummarized_messages(session_id=None)
    summaries = db.get_summaries(session_id)
    previous_context = "\n\n---\n\n".join(s.summary_text for s in summaries) if summaries else ""

    # 格式化对话 - 使用 content_processor，与 summarizer._format_conversation 一致
    from src.memory_core.prompts import ROLE_LABELS as SUMMARY_ROLE_LABELS
    lines = []
    total_chars = 0
    for msg in reversed(messages):
        role_label = SUMMARY_ROLE_LABELS.get(msg.role, msg.role)
        content = process_content(msg.content, content_config)
        if not content:
            continue  # 用户关闭了所有类型，跳过此消息
        line = f"{role_label}: {content}"
        if total_chars + len(line) > max_chars_total:
            break
        lines.insert(0, line)
        total_chars += len(line)
    conversation = "\n".join(lines)

    # 生成完整 prompt - 与 summarizer.generate 逻辑一致
    using_custom = False
    if custom_template and custom_template.strip():
        try:
            prompt = custom_template.format(previous_context=previous_context, conversation=conversation)
            using_custom = True
        except KeyError:
            custom_template = ""
    if not using_custom:
        if previous_context:
            prompt = SUMMARY_PROMPT_WITH_CONTEXT.format(previous_context=previous_context, conversation=conversation)
        else:
            prompt = SUMMARY_PROMPT.format(conversation=conversation)

    # 获取最新消息 ID 和最后一个 summary 的范围
    with db._connect() as conn:
        latest_msg = conn.execute("SELECT MAX(id) FROM messages").fetchone()
        latest_msg_id = latest_msg[0] if latest_msg and latest_msg[0] else 0
        last_summary = conn.execute("SELECT message_range_end FROM summaries ORDER BY id DESC LIMIT 1").fetchone()
        last_summary_end_id = last_summary[0] if last_summary and last_summary[0] else 0

    return json_response({
        "session_id": session_id,
        "message_count": len(messages),
        "summary_count": len(summaries),
        "latest_message_id": latest_msg_id,
        "last_summary_end_id": last_summary_end_id,
        "pending_count": latest_msg_id - last_summary_end_id if latest_msg_id > last_summary_end_id else 0,
        "using_custom_template": using_custom,
        "previous_context": previous_context[:1000] + "..." if len(previous_context) > 1000 else previous_context,
        "messages": [{"id": m.id, "role": m.role, "content": c, "is_summarized": m.is_summarized, "session_id": m.session_id, "model": m.model} for m in messages if (c := process_content(m.content, content_config))],
        "formatted_conversation": conversation,
        "full_prompt": prompt,
    })


# ========== Decisions API ==========

@app.route("/api/projects/<project_name>/decisions")
def get_decisions(project_name):
    """获取决策列表"""
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404

    status = request.args.get("status")  # pending, confirmed, skipped
    limit = int(request.args.get("limit", 100))

    db = Database(db_path)
    decisions = db.get_decisions(project=project_name, status=status, limit=limit)

    # 获取消息级别的 pending 信息
    latest_msg_id = db.get_latest_message_id()
    last_decision_id = db.get_last_decision_message_id()
    pending_msg_count = db.count_pending_decision_messages()

    return json_response({
        "decisions": [{
            "id": d.id,
            "project": d.project,
            "session_id": d.session_id,
            "problem": d.problem,
            "solution": d.solution,
            "status": d.status,
            "reason": d.reason,
            "reason_options": json.loads(d.reason_options) if d.reason_options else [],
            "note": d.note,
            "files": json.loads(d.files) if d.files else [],
            "tags": json.loads(d.tags) if d.tags else [],
            "message_range_start": d.message_range_start,
            "message_range_end": d.message_range_end,
            "message_count": d.message_count,
            "timestamp": d.timestamp.isoformat() if d.timestamp else None,
        } for d in decisions],
        "pending_count": db.count_pending_decisions(project_name),
        "latest_message_id": latest_msg_id,
        "last_decision_end_id": last_decision_id,
        "pending_message_count": pending_msg_count,
    })


@app.route("/api/projects/<project_name>/decisions/<int:decision_id>", methods=["PUT"])
def update_decision(project_name, decision_id):
    """更新决策（确认/跳过/添加备注）"""
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404

    data = request.get_json() or {}
    db = Database(db_path)

    # 允许更新的字段
    update_fields = {}
    if "status" in data:
        update_fields["status"] = data["status"]
    if "reason" in data:
        update_fields["reason"] = data["reason"]
    if "note" in data:
        update_fields["note"] = data["note"]
    if "tags" in data:
        update_fields["tags"] = json.dumps(data["tags"]) if isinstance(data["tags"], list) else data["tags"]

    if update_fields:
        success = db.update_decision(decision_id, **update_fields)
        if success:
            return json_response({"success": True, "message": "Decision updated"})
        return json_response({"error": "Decision not found"}), 404

    return json_response({"error": "No valid fields to update"}), 400


@app.route("/api/projects/<project_name>/decisions/<int:decision_id>", methods=["DELETE"])
def delete_decision(project_name, decision_id):
    """删除决策"""
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404

    db = Database(db_path)
    success = db.delete_decision(decision_id)

    if success:
        return json_response({"success": True, "message": "Decision deleted"})
    return json_response({"error": "Decision not found"}), 404


@app.route("/api/projects/<project_name>/decisions/search")
def search_decisions(project_name):
    """搜索已确认的决策"""
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404

    query = request.args.get("q", "")
    limit = int(request.args.get("limit", 20))

    if not query:
        return json_response({"error": "Query parameter 'q' is required"}), 400

    db = Database(db_path)
    decisions = db.search_decisions(query, project=project_name, limit=limit)

    return json_response({
        "query": query,
        "decisions": [{
            "id": d.id,
            "problem": d.problem,
            "solution": d.solution,
            "reason": d.reason,
            "note": d.note,
            "timestamp": d.timestamp.isoformat() if d.timestamp else None,
        } for d in decisions],
    })


@app.route("/api/projects/<project_name>/decisions/extract", methods=["POST"])
def extract_decisions(project_name):
    """手动触发决策提取"""
    from src.memory_core.events import publish_event
    from src.memory_core.decision_extractor import DecisionExtractor
    from src.memory_core.llm_client import create_llm_client
    from src.memory_core.config import load_config

    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404

    db = Database(db_path)
    config_mgr = load_config(GLOBAL_DB)

    # 获取未进行决策提取的消息
    messages = db.get_messages_for_decision(None)
    if len(messages) < 3:
        return json_response({"status": "ok", "message": "Not enough messages", "decisions": []})

    publish_event("decision", f"Extracting decisions from {len(messages)} messages", project_name)

    # 创建 LLM 客户端
    llm_client = create_llm_client(
        provider=config_mgr.get("llm_provider"),
        ollama_model=config_mgr.get("ollama_model"),
        ollama_base_url=config_mgr.get("ollama_base_url"),
        ollama_timeout=float(config_mgr.get("ollama_timeout") or 300),
        ollama_keep_alive=config_mgr.get("ollama_keep_alive"),
    )

    # 创建内容处理配置
    content_config = ContentConfig(
        include_thinking=config_mgr.get("content_include_thinking") == "true",
        include_tool=config_mgr.get("content_include_tool") == "true",
        include_text=config_mgr.get("content_include_text") == "true",
        max_chars_thinking=int(config_mgr.get("content_max_chars_thinking") or 200),
        max_chars_tool=int(config_mgr.get("content_max_chars_tool") or 300),
        max_chars_text=int(config_mgr.get("content_max_chars_text") or 500),
    )

    # 获取消息 ID 列表
    message_ids = [m.id for m in messages if m.id]

    # 提取决策（传入消息 ID 以记录范围）
    decision_prompt = config_mgr.get("decision_extraction_prompt") or ""
    extractor = DecisionExtractor(llm_client, content_config, decision_prompt)
    msg_list = [{"role": m.role, "content": m.content} for m in messages]
    decisions = extractor.extract_decisions(msg_list, project_name, "manual", message_ids=message_ids)

    # 保存决策
    saved_count = 0
    for decision in decisions:
        db.add_decision(decision)
        saved_count += 1

    # 标记消息为已决策提取
    if message_ids:
        db.mark_messages_decision_extracted(message_ids)

    if saved_count > 0:
        publish_event("decision_done", f"Extracted {saved_count} decisions", project_name)

    return json_response({
        "status": "ok",
        "message": f"Extracted {saved_count} decisions",
        "decisions": [{
            "problem": d.problem,
            "solution": d.solution,
        } for d in decisions],
    })


@app.route("/api/projects/<project_name>/decisions/debug")
def get_decision_debug(project_name):
    """获取决策提取的 prompt 预览"""
    from src.memory_core.prompts import DECISION_PROMPT
    from src.memory_core.config import load_config

    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404

    db = Database(db_path)
    config_mgr = load_config(GLOBAL_DB)

    # 获取未进行决策提取的消息
    messages = db.get_messages_for_decision(None)

    # 创建内容处理配置
    content_config = ContentConfig(
        include_thinking=config_mgr.get("content_include_thinking") == "true",
        include_tool=config_mgr.get("content_include_tool") == "true",
        include_text=config_mgr.get("content_include_text") == "true",
        max_chars_thinking=int(config_mgr.get("content_max_chars_thinking") or 200),
        max_chars_tool=int(config_mgr.get("content_max_chars_tool") or 300),
        max_chars_text=int(config_mgr.get("content_max_chars_text") or 500),
    )

    # 格式化对话（使用 ContentConfig）
    lines = []
    for msg in messages:
        role = msg.role
        content = process_content(msg.content, content_config)
        if content.strip():
            role_label = "User" if role == "user" else "Assistant"
            lines.append(f"{role_label}: {content}")

    conversation = "\n\n".join(lines)

    # 使用自定义 prompt 或默认
    custom_prompt = config_mgr.get("decision_extraction_prompt") or ""
    prompt_template = custom_prompt if custom_prompt.strip() else DECISION_PROMPT
    prompt = prompt_template.format(conversation=conversation)

    # 获取 pending 信息
    latest_msg_id = db.get_latest_message_id()
    last_decision_id = db.get_last_decision_message_id()
    pending_count = db.count_pending_decision_messages()

    return json_response({
        "prompt": prompt,
        "message_count": len(messages),
        "conversation_length": len(conversation),
        "latest_message_id": latest_msg_id,
        "last_decision_end_id": last_decision_id,
        "pending_count": pending_count,
    })


@app.route("/api/logs")
def get_logs():
    """获取合并的日志（app.log + hooks.log）"""
    lines = int(request.args.get("lines", 200))
    source = request.args.get("source", "all")  # all, app, hooks

    all_logs = []

    # 读取 app.log
    if source in ("all", "app") and LOG_FILE.exists():
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                all_logs.append({"source": "app", "line": line.rstrip()})

    # 读取 hooks.log
    hooks_log = MEMORY_BASE / "hooks.log"
    if source in ("all", "hooks") and hooks_log.exists():
        with open(hooks_log, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                all_logs.append({"source": "hooks", "line": line.rstrip()})

    # 按时间戳排序（假设日志格式以时间开头）
    # 简单策略：保持原顺序，取最后 N 行
    return json_response({"logs": all_logs[-lines:]})


@app.route("/api/logs/stream")
def stream_logs():
    """SSE 实时日志流"""
    def generate():
        last_pos_app = 0
        last_pos_hooks = 0
        hooks_log = MEMORY_BASE / "hooks.log"

        # 初始化位置到文件末尾
        if LOG_FILE.exists():
            last_pos_app = LOG_FILE.stat().st_size
        if hooks_log.exists():
            last_pos_hooks = hooks_log.stat().st_size

        yield f"data: {json.dumps({'type': 'connected', 'message': 'Log stream connected'})}\n\n"

        while True:
            new_lines = []

            # 检查 app.log 新内容
            if LOG_FILE.exists():
                current_size = LOG_FILE.stat().st_size
                if current_size > last_pos_app:
                    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(last_pos_app)
                        for line in f:
                            new_lines.append({"source": "app", "line": line.rstrip()})
                    last_pos_app = current_size
                elif current_size < last_pos_app:
                    # 文件被截断（rotation）
                    last_pos_app = 0

            # 检查 hooks.log 新内容
            if hooks_log.exists():
                current_size = hooks_log.stat().st_size
                if current_size > last_pos_hooks:
                    with open(hooks_log, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(last_pos_hooks)
                        for line in f:
                            new_lines.append({"source": "hooks", "line": line.rstrip()})
                    last_pos_hooks = current_size
                elif current_size < last_pos_hooks:
                    last_pos_hooks = 0

            if new_lines:
                for log in new_lines:
                    yield f"data: {json.dumps({'type': 'log', 'data': log}, ensure_ascii=False)}\n\n"

            time.sleep(0.5)  # 轮询间隔

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no',
    })


@app.route("/api/config")
def get_config():
    from src.memory_core.prompts import get_prompt
    db = Database(GLOBAL_DB)
    config_mgr = ConfigManager(db)
    return json_response({
        "config": config_mgr.get_all(),
        "defaults": DEFAULT_CONFIG,
        "meta": CONFIG_META,
        "default_prompts": {
            "summary_prompt_template": get_prompt("summary_with_context"),
            "knowledge_extraction_prompt": get_prompt("extraction"),
            "decision_extraction_prompt": get_prompt("decision"),
        },
        "i18n": {
            "category_names": get_prompt("category_names"),
            "role_labels": get_prompt("role_labels"),
            "ui_text": get_prompt("ui_text"),
        }
    })


@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.json
    db = Database(GLOBAL_DB)
    db.set_config(data["key"], data["value"])
    return json_response({"status": "ok"})


@app.route("/health")
def health():
    return json_response({"status": "ok"})


@app.route("/api/events")
def get_events():
    """获取最新的系统事件（用于实时通知）"""
    events_file = MEMORY_BASE / "events.json"
    if not events_file.exists():
        return json_response({"events": []})
    try:
        with open(events_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 只返回最近 30 秒内的事件
        from datetime import datetime
        now = datetime.now().timestamp()
        recent = [e for e in data.get("events", []) if now - e.get("timestamp", 0) < 30]
        return json_response({"events": recent})
    except:
        return json_response({"events": []})


@app.route("/api/events/clear", methods=["POST"])
def clear_events():
    """清除事件"""
    events_file = MEMORY_BASE / "events.json"
    if events_file.exists():
        events_file.unlink()
    return json_response({"status": "ok"})


@app.route("/api/projects/<project_name>/vectors/stats")
def get_vector_stats(project_name):
    """获取向量库统计信息（包括 Message 和 Decision）"""
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    try:
        from src.memory_core.vector_store import VectorStore
        from src.memory_core.config import load_config
        config_mgr = load_config(GLOBAL_DB)
        # Message 向量统计
        message_vector_store = VectorStore(db_path, store_type="message")
        message_stats = message_vector_store.get_stats()
        # Decision 向量统计
        decision_vector_store = VectorStore(db_path, store_type="decision")
        decision_stats = decision_vector_store.get_stats()
        return json_response({
            "message": message_stats,
            "decision": decision_stats,
            # 保持向后兼容
            "total_vectors": message_stats["total_vectors"],
            "mapped_messages": message_stats["mapped_messages"],
            "dimension": message_stats["dimension"],
        })
    except Exception as e:
        return json_response({"error": str(e)}), 500


@app.route("/api/projects/<project_name>/vectors/rebuild", methods=["POST"])
def rebuild_vectors(project_name):
    """重建向量数据库：清空后重新为所有消息生成 embedding"""
    from src.memory_core.events import publish_event

    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404

    try:
        from src.memory_core.config import load_config
        from src.memory_core.vector_store import VectorStore
        from src.memory_core.embedding_client import EmbeddingClient

        config_mgr = load_config(GLOBAL_DB)
        embedding_model = config_mgr.get("embedding_model")
        embedding_base_url = config_mgr.get("embedding_base_url") or config_mgr.get("ollama_base_url")

        publish_event("embedding", f"Rebuilding vectors for {project_name}", f"Model: {embedding_model}")

        # 清空向量库
        db = Database(db_path)
        vector_store = VectorStore(db_path)
        old_count = vector_store.get_stats()["total_vectors"]
        vector_store.clear()

        # 获取所有消息
        with db._connect() as conn:
            rows = conn.execute("SELECT id, content FROM messages ORDER BY id").fetchall()

        if not rows:
            return json_response({"status": "ok", "message": "No messages to index", "rebuilt": 0})

        # 创建 embedding 客户端
        embedding_client = EmbeddingClient(model=embedding_model, base_url=embedding_base_url)

        # 为每条消息生成 embedding
        rebuilt = 0
        import numpy as np
        for msg_id, content in rows:
            try:
                embedding = embedding_client.embed(content)
                if embedding is not None:
                    vector_store.add(msg_id, np.array(embedding))
                    rebuilt += 1
            except Exception as e:
                pass  # 跳过失败的消息

        publish_event("embedding", f"Rebuilt {rebuilt} vectors", f"Cleared {old_count}, new {rebuilt}")

        return json_response({
            "status": "ok",
            "old_count": old_count,
            "rebuilt": rebuilt,
            "total_messages": len(rows),
        })
    except Exception as e:
        publish_event("error", f"Rebuild failed: {str(e)}", project_name)
        return json_response({"error": str(e)}), 500


@app.route("/api/projects/<project_name>/vectors/rebuild-decisions", methods=["POST"])
def rebuild_decision_vectors(project_name):
    """重建 Decision 向量数据库"""
    from src.memory_core.events import publish_event

    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404

    try:
        from src.memory_core.config import load_config
        from src.memory_core.vector_store import VectorStore
        from src.memory_core.embedding_client import EmbeddingClient

        config_mgr = load_config(GLOBAL_DB)
        embedding_model = config_mgr.get("embedding_model")
        embedding_base_url = config_mgr.get("embedding_base_url") or config_mgr.get("ollama_base_url")

        publish_event("embedding", f"Rebuilding decision vectors for {project_name}", f"Model: {embedding_model}")

        db = Database(db_path)
        vector_store = VectorStore(db_path, store_type="decision")
        old_count = vector_store.get_stats()["total_vectors"]
        vector_store.clear()

        # 获取所有已确认的决策
        decisions = db.get_decisions(status="confirmed", limit=1000)

        if not decisions:
            return json_response({"status": "ok", "message": "No decisions to index", "rebuilt": 0})

        embedding_client = EmbeddingClient(model=embedding_model, base_url=embedding_base_url)

        rebuilt = 0
        import numpy as np
        for decision in decisions:
            try:
                content = f"{decision.problem}\n{decision.solution}"
                if decision.reason:
                    content += f"\n{decision.reason}"
                embedding = embedding_client.embed(content)
                if embedding is not None:
                    vector_store.add(decision.id, np.array(embedding))
                    rebuilt += 1
            except Exception as e:
                pass

        publish_event("embedding", f"Rebuilt {rebuilt} decision vectors", f"Cleared {old_count}, new {rebuilt}")

        return json_response({
            "status": "ok",
            "old_count": old_count,
            "rebuilt": rebuilt,
            "total_decisions": len(decisions),
        })
    except Exception as e:
        publish_event("error", f"Decision rebuild failed: {str(e)}", project_name)
        return json_response({"error": str(e)}), 500


# ============ Web UI ============

@app.route("/")
def index():
    return """<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hybrid Memory Dashboard</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        /* Scrollbar styling */
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: #1a1a2e; }
        ::-webkit-scrollbar-thumb { background: #333; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #444; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eee; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 10px; }
        h1 { color: #00d9ff; }
        .global-project { display: flex; align-items: center; gap: 10px; }
        .global-project label { color: #888; }
        .global-project select { padding: 10px 15px; font-size: 1em; }
        h2 { color: #00d9ff; margin: 20px 0 10px; font-size: 1.2em; }
        .tabs { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
        .tab { padding: 10px 20px; background: #16213e; border: none; color: #eee; cursor: pointer; border-radius: 5px; }
        .tab:hover { background: #1f4068; }
        .tab.active { background: #00d9ff; color: #1a1a2e; }
        .panel { display: none; }
        .panel.active { display: block; }
        #messages { flex-direction: column; height: calc(100vh - 180px); padding: 0; }
        #messages.active { display: flex !important; }
        .card { background: #16213e; border-radius: 8px; padding: 15px; margin-bottom: 15px; }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .badge { background: #00d9ff; color: #1a1a2e; padding: 2px 8px; border-radius: 10px; font-size: 0.8em; }
        .message { padding: 10px; margin: 5px 0; border-radius: 5px; }
        .message.user { background: #1f4068; border-left: 3px solid #00d9ff; }
        .message.assistant { background: #0f3460; border-left: 3px solid #e94560; }
        .message-role { font-weight: bold; color: #00d9ff; margin-bottom: 5px; }
        .message-content { white-space: pre-wrap; word-break: break-word; font-size: 0.9em; line-height: 1.5; }
        .message-meta { font-size: 0.75em; color: #888; margin-top: 5px; }
        .summary { background: #1f4068; padding: 15px; border-radius: 5px; margin: 10px 0; border-left: 3px solid #e94560; }
        .summary-text { white-space: pre-wrap; line-height: 1.6; }
        select, input { padding: 8px 12px; border-radius: 5px; border: 1px solid #333; background: #0f3460; color: #eee; margin-right: 10px; }
        button { padding: 8px 16px; border-radius: 5px; border: none; background: #00d9ff; color: #1a1a2e; cursor: pointer; }
        button:hover { background: #00b4d8; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 15px; }
        .stat { text-align: center; }
        .stat-value { font-size: 2em; color: #00d9ff; }
        .stat-label { color: #888; }
        /* Terminal-style log container */
        .terminal-log {
            background: #0a0a0a;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 10px;
            height: 500px;
            overflow-y: auto;
            font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            font-size: 0.8em;
            line-height: 1.4;
        }
        .log-line { padding: 1px 5px; white-space: pre-wrap; word-break: break-all; border-radius: 2px; margin: 1px 0; }
        .log-line:hover { background: #1a1a1a; }
        .log-line.error { color: #ff6b6b; background: rgba(255,107,107,0.1); }
        .log-line.warning { color: #ffa726; }
        .log-line.info { color: #4fc3f7; }
        .log-line.debug { color: #888; }
        .log-line .log-time { color: #666; }
        .log-line .log-level { font-weight: bold; }
        .log-line .log-source { color: #9c27b0; font-size: 0.85em; }
        .log-line.hidden { display: none; }
        .search-box { display: flex; gap: 10px; margin-bottom: 15px; }
        .search-box input { flex: 1; }
        .context-preview { background: #0f3460; padding: 15px; border-radius: 8px; margin: 10px 0; }
        .context-section { margin: 10px 0; }
        .context-label { color: #00d9ff; font-weight: bold; margin-bottom: 5px; }
        .session-select { margin-bottom: 15px; }
        .no-project { color: #888; padding: 20px; text-align: center; }
        /* Modal styles */
        .modal { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 1000; }
        .modal-overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); }
        .modal-content { position: relative; max-width: 700px; max-height: 85vh; margin: 50px auto; background: #16213e; border-radius: 12px; overflow: hidden; display: flex; flex-direction: column; }
        .modal-header { display: flex; justify-content: space-between; align-items: center; padding: 15px 20px; border-bottom: 1px solid #333; }
        .modal-body { padding: 20px; overflow-y: auto; flex: 1; }
        .modal-footer { padding: 15px 20px; border-top: 1px solid #333; display: flex; justify-content: flex-end; }
        /* Event toast animation */
        @keyframes slideIn {
            from { opacity: 0; transform: translateX(-20px); }
            to { opacity: 1; transform: translateX(0); }
        }
        /* Tooltip styles - 使用 data-tooltip 避免浏览器默认 title */
        .tooltip-icon {
            display: inline-flex; align-items: center; justify-content: center;
            width: 18px; height: 18px; border-radius: 50%;
            background: #4a90d9; color: #fff; font-size: 12px; font-weight: bold;
            cursor: help; position: relative;
        }
        .tooltip-icon:hover::after {
            content: attr(data-tooltip);
            position: absolute; left: 0; top: 100%;
            background: #1a1a2e; color: #eee; padding: 10px 12px;
            border-radius: 6px; font-size: 12px; font-weight: normal;
            white-space: pre-wrap; width: 280px;
            z-index: 9999; margin-top: 8px;
            border: 1px solid #4a90d9; box-shadow: 0 4px 12px rgba(0,0,0,0.5);
            line-height: 1.5;
        }
        .tooltip-icon:hover::before {
            content: ''; position: absolute; left: 8px; top: 100%;
            border: 6px solid transparent; border-bottom-color: #4a90d9;
            z-index: 10000;
        }
    </style>
</head>
<body>
    <!-- Event Notifications -->
    <div id="event-panel" style="position: fixed; top: 10px; left: 10px; z-index: 9999; max-width: 350px;"></div>

    <div class="container">
        <div class="header">
            <h1>Hybrid Memory Dashboard</h1>
            <div style="display: flex; align-items: center; gap: 15px;">
                <div class="global-project">
                    <label>Current Project:</label>
                    <select id="global-project" onchange="onProjectChange()">
                        <option value="">-- Select Project --</option>
                    </select>
                </div>
                <button onclick="openConfigModal()" style="padding: 8px 12px; background: #16213e; border: 1px solid #333; font-size: 1.2em;" title="Settings">⚙️</button>
            </div>
        </div>

        <div class="tabs">
            <button class="tab active" onclick="showPanel('overview')">Overview</button>
            <button class="tab" onclick="showPanel('messages')">Messages</button>
            <button class="tab" onclick="showPanel('search')">Search</button>
            <button class="tab" onclick="showPanel('context')">Injected Context</button>
            <button class="tab" onclick="showPanel('summaries')">Summaries</button>
            <button class="tab" onclick="showPanel('knowledge')">Knowledge</button>
            <button class="tab" onclick="showPanel('decisions')">Decisions <span id="pending-decisions-badge" class="badge" style="display:none;"></span></button>
            <button class="tab" onclick="showPanel('logs')">Logs</button>
        </div>

        <!-- Overview Panel -->
        <div id="overview" class="panel active">
            <h2>System Overview</h2>
            <div class="grid" id="stats"></div>
            <h2>All Projects</h2>
            <div id="project-list" class="grid"></div>
        </div>

        <!-- Messages Panel -->
        <div id="messages" class="panel">
            <div style="padding: 15px 20px; border-bottom: 1px solid #333; background: #16213e; position: sticky; top: 0; z-index: 10;">
                <h2 style="margin: 0 0 10px 0;">Messages</h2>
                <div class="session-select" style="display: flex; align-items: center; gap: 15px; flex-wrap: wrap;">
                    <div>
                        <label style="color: #888; font-size: 0.9em;">Session:</label>
                        <select id="msg-session" onchange="onSessionChange()" style="margin-left: 5px;">
                            <option value="">All Sessions</option>
                        </select>
                    </div>
                    <div id="session-id-display" style="font-size: 0.8em; color: #666; font-family: monospace; display: none;">
                        ID: <span id="session-id-text"></span>
                    </div>
                </div>
            </div>
            <div id="message-list" style="flex: 1; overflow-y: auto; padding: 20px; scroll-behavior: smooth;"></div>
            <button id="scroll-to-bottom-btn" onclick="scrollMessagesToBottom()" style="display: none; position: fixed; bottom: 30px; right: 30px; width: 50px; height: 50px; border-radius: 50%; background: #00d9ff; color: #1a1a2e; font-size: 1.5em; cursor: pointer; z-index: 100; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">↓</button>
        </div>

        <!-- Summaries Panel -->
        <div id="summaries" class="panel">
            <h2>Summaries</h2>
            <div style="margin-bottom: 15px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center;">
                <button onclick="triggerSummary()" style="background: #4a90d9;">Summarize Now</button>
                <button onclick="regenerateAllSummaries()" style="background: #e94560;">Regenerate All</button>
                <button onclick="saveSummarySelection()">Save Extra Selection</button>
                <span id="selection-dirty-hint" style="color: #ffcc00; margin-left: 5px; display: none;">● Unsaved</span>
                <span style="flex: 1;"></span>
                <button onclick="toggleSummaryDebug()" style="background: #1f4068; padding: 6px 12px;">🔍 Debug</button>
            </div>

            <!-- Summary Debug Panel (collapsible) -->
            <div id="summary-debug-panel" style="display: none; margin-bottom: 20px;">
                <div class="card" style="border-left: 3px solid #4a90d9;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                        <h3 style="margin: 0; color: #4a90d9;">Summary Debug - LLM Prompt Preview</h3>
                        <button onclick="document.getElementById('summary-debug-panel').style.display='none'" style="background: #333; padding: 4px 10px;">Close</button>
                    </div>
                    <div style="margin-bottom: 10px;">
                        <button onclick="loadSummaryDebug()" style="padding: 6px 14px;">Load Prompt</button>
                    </div>
                    <div id="debug-info" style="color: #888; margin-bottom: 10px;"></div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px;">
                        <div>
                            <h4 style="color: #00d9ff; margin-bottom: 5px;">Unsummarized Messages (<span id="debug-msg-count">0</span>)</h4>
                            <div id="debug-messages" style="max-height: 200px; overflow-y: auto;"></div>
                        </div>
                        <div>
                            <h4 style="color: #e94560; margin-bottom: 5px;">Full Prompt to LLM</h4>
                            <pre id="debug-prompt" style="background: #1a1a2e; padding: 10px; border-radius: 6px; white-space: pre-wrap; word-wrap: break-word; font-size: 0.8em; max-height: 200px; overflow-y: auto;"></pre>
                        </div>
                    </div>
                </div>
            </div>

            <div style="display: flex; gap: 20px;">
                <div style="flex: 1;">
                    <h3 style="color: #888; margin-bottom: 10px;">Available (click to add to extras)</h3>
                    <div id="summary-list-all" style="max-height: 70vh; overflow-y: auto;"></div>
                </div>
                <div style="flex: 1;">
                    <h3 style="color: #00d9ff; margin-bottom: 10px;">Will Be Injected (top to bottom)</h3>
                    <div style="background: #1f3460; border-radius: 8px; padding: 10px; margin-bottom: 15px;">
                        <div style="color: #e94560; font-weight: bold; margin-bottom: 8px;">➕ Extra Selected (background context, drag to reorder)</div>
                        <div id="summary-list-extra"></div>
                    </div>
                    <div style="background: #0f3460; border-radius: 8px; padding: 10px;">
                        <div style="color: #00d9ff; font-weight: bold; margin-bottom: 8px;">📌 Latest <span id="inject-count-display">N</span> (Auto, old → new)</div>
                        <div id="summary-list-auto"></div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Context Panel -->
        <div id="context" class="panel">
            <h2>Injected Context Preview</h2>
            <p style="color: #888; margin-bottom: 15px;">This shows what would be injected into Claude when starting a session.</p>
            <div id="context-preview"></div>
        </div>

        <!-- Search Panel -->
        <div id="search" class="panel">
            <h2>Search Records</h2>
            <div class="card" style="margin-bottom: 15px;">
                <div style="display: flex; gap: 15px; flex-wrap: wrap; align-items: center; margin-bottom: 15px;">
                    <div style="flex: 1; min-width: 200px;">
                        <input type="text" id="search-query" placeholder="Enter search query..." onkeypress="if(event.key==='Enter')doUnifiedSearch()" style="width: 100%; padding: 12px;">
                    </div>
                    <button onclick="doUnifiedSearch()" style="padding: 12px 24px;">Search</button>
                </div>
                <div style="display: flex; gap: 20px; flex-wrap: wrap; align-items: center;">
                    <div>
                        <label style="color: #888; font-size: 0.9em;">Type:</label>
                        <select id="search-type" style="margin-left: 5px;" onchange="onSearchTypeChange()">
                            <option value="message">Messages</option>
                            <option value="decision">Decisions</option>
                        </select>
                    </div>
                    <div>
                        <label style="color: #888; font-size: 0.9em;">Scope:</label>
                        <select id="search-scope" style="margin-left: 5px;">
                            <option value="current">Current Project</option>
                            <option value="all">All Projects</option>
                        </select>
                    </div>
                    <div>
                        <label style="color: #888; font-size: 0.9em;">Method:</label>
                        <select id="search-method" style="margin-left: 5px;" onchange="onSearchMethodChange()">
                            <option value="combined">Combined</option>
                            <option value="vector">Vector (Semantic)</option>
                            <option value="bm25">BM25 (Keyword)</option>
                            <option value="fuzzy">Fuzzy (Partial Match)</option>
                        </select>
                    </div>
                    <div id="fuzzy-options" style="display: none;">
                        <label style="color: #888; font-size: 0.9em;">Threshold:</label>
                        <input type="number" id="fuzzy-threshold" value="60" min="0" max="100" style="width: 60px; padding: 5px; margin-left: 5px;">
                    </div>
                </div>
            </div>

            <!-- Vector Database Stats & Rebuild -->
            <div class="card" style="margin-bottom: 15px; border-left: 3px solid #d9a04a;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                    <div>
                        <span style="color: #d9a04a; font-weight: bold;">🔢 Vector Database</span>
                        <span id="vector-stats" style="color: #888; margin-left: 10px; font-size: 0.9em;">Loading...</span>
                    </div>
                    <div style="display: flex; gap: 8px;">
                        <button onclick="loadVectorStats()" style="padding: 6px 12px; background: #1f4068;">Refresh</button>
                        <button onclick="rebuildVectors()" style="padding: 6px 12px; background: #e94560;">Rebuild Messages</button>
                        <button onclick="rebuildDecisionVectors()" style="padding: 6px 12px; background: #6c5ce7;">Rebuild Decisions</button>
                    </div>
                </div>
                <div style="color: #666; font-size: 0.8em; margin-top: 8px;">
                    ⚠️ 更换 Embedding 模型后需重建向量库才能正常搜索
                </div>
            </div>

            <div style="margin-bottom: 10px; color: #666; font-size: 0.85em;">
                <span id="search-status"></span>
            </div>
            <div id="search-results"></div>
        </div>

        <!-- Logs Panel -->
        <div id="logs" class="panel">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <h2 style="margin: 0;">System Logs</h2>
                <div style="display: flex; gap: 10px; align-items: center;">
                    <select id="log-source" onchange="loadLogs()" style="padding: 5px;">
                        <option value="all">All Sources</option>
                        <option value="app">App Only</option>
                        <option value="hooks">Hooks Only</option>
                    </select>
                    <select id="log-level" onchange="filterLogLevel()" style="padding: 5px;">
                        <option value="all">All Levels</option>
                        <option value="error">ERROR</option>
                        <option value="warning">WARNING</option>
                        <option value="info">INFO</option>
                        <option value="debug">DEBUG</option>
                    </select>
                    <label style="color: #888; font-size: 0.85em; display: flex; align-items: center; gap: 5px;">
                        <input type="checkbox" id="log-autoscroll" checked> Auto-scroll
                    </label>
                    <label style="color: #888; font-size: 0.85em; display: flex; align-items: center; gap: 5px;">
                        <input type="checkbox" id="log-realtime" onchange="toggleRealtimeLogs()"> Realtime
                    </label>
                    <button onclick="loadLogs()" style="padding: 5px 15px;">Refresh</button>
                    <button onclick="clearLogDisplay()" style="padding: 5px 15px; background: #333;">Clear</button>
                </div>
            </div>
            <div id="log-status" style="font-size: 0.8em; color: #888; margin-bottom: 5px;"></div>
            <div id="log-list" class="terminal-log"></div>
        </div>

        <!-- Config Modal -->
        <div id="config-modal" class="modal" style="display: none;">
            <div class="modal-overlay" onclick="closeConfigModal()"></div>
            <div class="modal-content" style="max-width: 95vw; width: 1200px;">
                <div class="modal-header">
                    <h2 style="margin: 0; color: #00d9ff;">Settings</h2>
                    <button onclick="closeConfigModal()" style="background: none; border: none; color: #888; font-size: 1.5em; cursor: pointer;">&times;</button>
                </div>
                <div class="modal-body">
                    <div id="config-form"></div>
                </div>
                <div class="modal-footer">
                    <button onclick="saveAllConfig()" style="padding: 12px 30px; font-size: 1.1em;">Save All Settings</button>
                    <button onclick="resetConfig()" style="background: #e94560; margin-left: 10px;">Reset to Defaults</button>
                    <button onclick="closeConfigModal()" style="background: #333; margin-left: 10px;">Cancel</button>
                </div>
            </div>
        </div>

        <!-- Summary Messages Modal -->
        <div id="summary-messages-modal" class="modal" style="display: none;">
            <div class="modal-overlay" onclick="closeSummaryMessagesModal()"></div>
            <div class="modal-content" style="max-width: 1000px; max-height: 90vh; margin: 20px auto;">
                <div class="modal-header">
                    <h2 style="margin: 0; color: #00d9ff;">Original Messages for Summary</h2>
                    <button onclick="closeSummaryMessagesModal()" style="background: none; border: none; color: #888; font-size: 1.5em; cursor: pointer;">&times;</button>
                </div>
                <div class="modal-body" style="max-height: 80vh; overflow-y: auto;">
                    <div id="summary-messages-content"></div>
                </div>
            </div>
        </div>

        <!-- Knowledge Panel -->
        <div id="knowledge" class="panel">
            <h2>Structured Knowledge</h2>
            <div style="display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 15px; align-items: center;">
                <button onclick="loadKnowledge()" title="Reload current knowledge from database">Refresh</button>
                <button onclick="extractKnowledge()" style="background: #4a90d9;" title="Extract NEW knowledge from unsummarized messages (fuses with existing)">Extract New</button>
                <button onclick="loadKnowledgeDebug()" style="background: #6c5ce7; color: #fff;" title="View the prompt that will be sent to LLM">View Prompt</button>
                <span id="knowledge-status" style="color: #888; font-size: 0.85em; margin-left: 10px;"></span>
            </div>
            <div id="knowledge-pending-info" style="color: #888; margin-bottom: 10px;"></div>
            <div id="knowledge-debug-panel" style="display: none; margin-bottom: 15px;">
                <div class="card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                        <h3 style="margin: 0;">Knowledge Extraction Prompt</h3>
                        <button onclick="document.getElementById('knowledge-debug-panel').style.display='none'" style="background: #e94560; color: #fff; padding: 4px 12px;">Close</button>
                    </div>
                    <div id="knowledge-debug-info" style="color: #888; margin-bottom: 10px;"></div>
                    <div style="margin-bottom: 10px;">
                        <h4 style="color: #00d9ff; margin-bottom: 5px;">Input Messages</h4>
                        <div id="knowledge-debug-messages" style="max-height: 200px; overflow-y: auto;"></div>
                    </div>
                    <div style="margin-bottom: 10px;">
                        <h4 style="color: #e94560; margin-bottom: 5px;">Existing Knowledge (Context)</h4>
                        <pre id="knowledge-debug-existing" style="background: #1a1a2e; padding: 10px; border-radius: 6px; white-space: pre-wrap; font-size: 0.85em; max-height: 150px; overflow-y: auto; color: #888;"></pre>
                    </div>
                    <div>
                        <h4 style="color: #00d9ff; margin-bottom: 5px;">Full Prompt</h4>
                        <pre id="knowledge-debug-prompt" style="background: #1a1a2e; padding: 10px; border-radius: 6px; white-space: pre-wrap; font-size: 0.85em; max-height: 300px; overflow-y: auto;"></pre>
                    </div>
                </div>
            </div>

            <!-- Knowledge History Section -->
            <div style="display: grid; grid-template-columns: 200px 1fr; gap: 15px; margin-top: 15px;">
                <div>
                    <h3 style="color: #00d9ff; margin-bottom: 10px;">History</h3>
                    <div id="knowledge-history-list" style="max-height: 400px; overflow-y: auto;"></div>
                </div>
                <div id="knowledge-content">
                    <div class="card">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <h3>User Preferences</h3>
                            <span id="knowledge-viewing-label" style="color: #888; font-size: 0.8em;"></span>
                        </div>
                        <ul id="k-user-preferences"></ul>
                    </div>
                    <div class="card" style="margin-top: 15px;">
                        <h3>Project Decisions</h3>
                        <ul id="k-project-decisions"></ul>
                    </div>
                    <div class="card" style="margin-top: 15px;">
                        <h3>Key Facts</h3>
                        <ul id="k-key-facts"></ul>
                    </div>
                    <div class="card" style="margin-top: 15px;">
                        <h3>Pending Tasks</h3>
                        <ul id="k-pending-tasks"></ul>
                    </div>
                    <div class="card" style="margin-top: 15px;">
                        <h3>Learned Patterns</h3>
                        <ul id="k-learned-patterns"></ul>
                    </div>
                    <div class="card" style="margin-top: 15px;">
                        <h3>Important Context</h3>
                        <ul id="k-important-context"></ul>
                    </div>
                </div>
            </div>
        </div>

        <!-- Decisions Panel -->
        <div id="decisions" class="panel">
            <h2>Decisions</h2>
            <div style="display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 15px; align-items: center;">
                <button onclick="loadDecisions()" title="Refresh decisions list">Refresh</button>
                <button onclick="extractDecisions()" style="background: #4a90d9;" title="Extract decisions from recent conversation">Extract Now</button>
                <button onclick="loadDecisionDebug()" style="background: #6c5ce7; color: #fff;" title="View the prompt that will be sent to LLM">View Prompt</button>
                <button onclick="saveDecisionSelection()">Save Extra Selection</button>
                <span id="decision-selection-dirty-hint" style="color: #ffcc00; margin-left: 5px; display: none;">● Unsaved</span>
                <span style="flex: 1;"></span>
                <span id="decisions-status" style="color: #888; font-size: 0.85em;"></span>
            </div>
            <div id="decision-pending-info" style="color: #888; margin-bottom: 10px;"></div>
            <div id="decision-debug-panel" style="display: none; margin-bottom: 15px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
                    <span style="color: #6c5ce7; font-weight: bold;">Decision Extraction Prompt Preview</span>
                    <button onclick="document.getElementById('decision-debug-panel').style.display='none'" style="background: #333; padding: 2px 8px;">Close</button>
                </div>
                <pre id="decision-debug-content" style="background: #1a1a2e; padding: 10px; border-radius: 5px; max-height: 400px; overflow: auto; white-space: pre-wrap; font-size: 0.85em; border: 1px solid #6c5ce7;"></pre>
            </div>

            <!-- Pending Decisions Section -->
            <div id="pending-decisions-section" style="margin-bottom: 20px;">
                <h3 style="color: #e94560; margin-bottom: 10px;">Pending Decisions <span id="pending-count-label"></span></h3>
                <div id="pending-decisions-list"></div>
            </div>

            <!-- Two-column layout for Confirmed Decisions -->
            <div style="display: flex; gap: 20px;">
                <div style="flex: 1;">
                    <h3 style="color: #888; margin-bottom: 10px;">Confirmed (click to add to extras)</h3>
                    <div id="decision-list-all" style="max-height: 60vh; overflow-y: auto;"></div>
                </div>
                <div style="flex: 1;">
                    <h3 style="color: #00d9ff; margin-bottom: 10px;">Will Be Injected</h3>
                    <div style="background: #1f3460; border-radius: 8px; padding: 10px; margin-bottom: 15px;">
                        <div style="color: #e94560; font-weight: bold; margin-bottom: 8px;">➕ Extra Selected</div>
                        <div id="decision-list-extra"></div>
                    </div>
                    <div style="background: #0f3460; border-radius: 8px; padding: 10px;">
                        <div style="color: #00d9ff; font-weight: bold; margin-bottom: 8px;">📌 Latest <span id="decision-inject-count-display">N</span> (Auto)</div>
                        <div id="decision-list-auto"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let projects = [];
        let currentProject = '';
        let currentPanel = 'overview';
        let currentSession = '';
        let sessionsData = [];
        let interactionsData = [];  // 存储权限请求和用户选择等交互记录
        let expandedInteractions = new Set();  // 记录已展开的交互面板 ID
        // 全局配置（从后端加载）
        let appConfig = {
            summary_max_chars_total: 8000,
            search_result_preview_length: 500,
            dashboard_refresh_interval: 5000,
        };

        function showPanel(id) {
            currentPanel = id;
            document.querySelectorAll('.panel').forEach(p => {
                p.classList.remove('active');
                p.style.display = 'none';
            });
            const panel = document.getElementById(id);
            panel.classList.add('active');
            // Messages panel needs flex display
            if (id === 'messages') {
                panel.style.display = 'flex';
            } else {
                panel.style.display = 'block';
            }
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
            // Update scroll button visibility
            updateScrollButtonVisibility();
            // Auto-load data for specific panels
            if (id === 'knowledge' && currentProject) {
                loadKnowledge();
            } else if (id === 'decisions' && currentProject) {
                loadDecisions();
            } else if (id === 'search' && currentProject) {
                loadVectorStats();
            } else if (id === 'logs') {
                loadLogs();
            }
        }

        function updateScrollButtonVisibility() {
            const btn = document.getElementById('scroll-to-bottom-btn');
            if (!btn) return;
            // Only show in messages panel
            if (currentPanel !== 'messages') {
                btn.style.display = 'none';
                return;
            }
            const listEl = document.getElementById('message-list');
            if (!listEl) return;
            const isNearBottom = listEl.scrollHeight - listEl.scrollTop - listEl.clientHeight < 200;
            btn.style.display = isNearBottom ? 'none' : 'block';
        }

        function onProjectChange() {
            currentProject = document.getElementById('global-project').value;
            currentSession = '';  // Reset session when project changes
            localStorage.setItem('currentProject', currentProject);
            loadProjectData();
        }

        function loadProjectData() {
            if (!currentProject) {
                document.getElementById('message-list').innerHTML = '<div class="no-project">Please select a project</div>';
                document.getElementById('context-preview').innerHTML = '<div class="no-project">Please select a project</div>';
                return;
            }
            loadSessions();
            loadMessages();
            loadVectorStats();
            loadSummaries();
            loadContext();
        }

        async function loadProjects() {
            const res = await fetch('/api/projects');
            const data = await res.json();
            projects = data.projects;
            const totals = data.totals || {};

            // 格式化 token 数量
            const formatTokens = (n) => {
                if (n >= 1000000) return (n / 1000000).toFixed(2) + 'M';
                if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
                return n.toString();
            };

            // Stats
            let totalMsgs = 0, totalSums = 0;
            projects.forEach(p => {
                totalMsgs += p.messages || 0;
                totalSums += p.summaries || 0;
            });
            document.getElementById('stats').innerHTML = `
                <div class="card stat"><div class="stat-value">${projects.length}</div><div class="stat-label">Projects</div></div>
                <div class="card stat"><div class="stat-value">${totalMsgs}</div><div class="stat-label">Total Messages</div></div>
                <div class="card stat"><div class="stat-value">${totalSums}</div><div class="stat-label">Summaries</div></div>
                <div class="card stat"><div class="stat-value">${formatTokens(totals.input_tokens || 0)}</div><div class="stat-label">Input Tokens</div></div>
                <div class="card stat"><div class="stat-value">${formatTokens(totals.output_tokens || 0)}</div><div class="stat-label">Output Tokens</div></div>
                <div class="card stat" style="background: linear-gradient(135deg, #1a1a2e 0%, #2d1f3d 100%);"><div class="stat-value" style="color: #f39c12;">$${(totals.total_cost || 0).toFixed(2)}</div><div class="stat-label">Total Cost</div></div>
            `;

            // Project list
            document.getElementById('project-list').innerHTML = projects.map(p => `
                <div class="card" style="cursor: pointer;" onclick="selectProject('${p.name}')">
                    <div class="card-header"><strong>${p.name}</strong><span class="badge">${p.messages || 0} msgs</span></div>
                    <div>Sessions: ${p.sessions || 0} | Summaries: ${p.summaries || 0}</div>
                    <div style="margin-top: 6px; font-size: 12px; color: #aaa;">
                        <span title="Input tokens">📥 ${formatTokens(p.input_tokens || 0)}</span>
                        <span style="margin-left: 8px;" title="Output tokens">📤 ${formatTokens(p.output_tokens || 0)}</span>
                        <span style="margin-left: 8px; color: #f39c12;" title="Cost">💰 $${(p.cost || 0).toFixed(4)}</span>
                    </div>
                </div>
            `).join('');

            // Global dropdown
            const opts = projects.map(p => `<option value="${p.name}" ${p.name === currentProject ? 'selected' : ''}>${p.name}</option>`).join('');
            document.getElementById('global-project').innerHTML = '<option value="">-- Select Project --</option>' + opts;

            // Auto-select first or saved
            if (!currentProject && projects.length > 0) {
                const saved = localStorage.getItem('currentProject');
                if (saved && projects.find(p => p.name === saved)) {
                    currentProject = saved;
                } else {
                    currentProject = projects[0].name;
                }
                document.getElementById('global-project').value = currentProject;
            }
        }

        function selectProject(name) {
            currentProject = name;
            currentSession = '';  // Reset session when project changes
            document.getElementById('global-project').value = name;
            localStorage.setItem('currentProject', name);
            loadProjectData();
        }

        async function loadSessions() {
            if (!currentProject) return;
            const res = await fetch(`/api/projects/${currentProject}/sessions`);
            const data = await res.json();
            sessionsData = data.sessions || [];

            // 格式化时间显示（精确到秒）
            const formatTime = (ts) => {
                if (!ts) return 'Unknown';
                const d = new Date(ts);
                return d.toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit'});
            };

            const opts = sessionsData.map(s => {
                const timeLabel = formatTime(s.started_at);
                const activeLabel = s.is_active ? ' 🟢' : '';
                return `<option value="${s.session_id}">${timeLabel}${activeLabel}</option>`;
            }).join('');

            const selectEl = document.getElementById('msg-session');
            selectEl.innerHTML = '<option value="">All Sessions</option>' + opts;

            // 恢复之前选中的 session
            if (currentSession && sessionsData.find(s => s.session_id === currentSession)) {
                selectEl.value = currentSession;
            }
            updateSessionIdDisplay();
        }

        function onSessionChange() {
            currentSession = document.getElementById('msg-session').value;
            updateSessionIdDisplay();
            loadMessages();
        }

        function updateSessionIdDisplay() {
            const display = document.getElementById('session-id-display');
            const text = document.getElementById('session-id-text');
            if (currentSession) {
                display.style.display = 'block';
                text.textContent = currentSession;
            } else {
                display.style.display = 'none';
            }
        }

        let messagesInitialized = false;

        // 简单 Markdown 渲染
        function renderMarkdown(text) {
            let html = escapeHtml(text);
            // 代码块 ```...```
            html = html.replace(/```(\\w*)\\n([\\s\\S]*?)```/g, '<pre style="background:#1a1a2e;padding:8px;border-radius:4px;overflow-x:auto;"><code>$2</code></pre>');
            // ASCII 表格（包含 │├└┌┐┘─ 等 box-drawing 字符的连续行）
            html = html.replace(/((?:.*[│├└┌┐┘┬┴┼─┤╭╮╰╯]+.*\\n?)+)/g, '<pre style="background:#1a1a2e;padding:8px;border-radius:4px;overflow-x:auto;font-family:monospace;line-height:1.2;">$1</pre>');
            // Markdown 表格 (| col | col | 格式)
            html = html.replace(/(\\|[^\\n]+\\|\\n\\|[-:\\| ]+\\|\\n(?:\\|[^\\n]+\\|\\n?)+)/g, function(match) {
                const lines = match.trim().split('\\n');
                if (lines.length < 2) return match;
                const headerCells = lines[0].split('|').filter(c => c.trim());
                const rows = lines.slice(2).map(line => line.split('|').filter(c => c.trim()));
                let table = '<table style="border-collapse:collapse;background:#1a1a2e;border-radius:4px;margin:8px 0;width:100%;">';
                table += '<tr>' + headerCells.map(c => '<th style="border:1px solid #333;padding:6px 10px;color:#00d9ff;text-align:left;">' + c.trim() + '</th>').join('') + '</tr>';
                rows.forEach(row => {
                    table += '<tr>' + row.map(c => '<td style="border:1px solid #333;padding:6px 10px;">' + c.trim() + '</td>').join('') + '</tr>';
                });
                table += '</table>';
                return table;
            });
            // 行内代码 `...`
            html = html.replace(/`([^`]+)`/g, '<code style="background:#1a1a2e;padding:2px 4px;border-radius:3px;">$1</code>');
            // 粗体 **...**
            html = html.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
            // 斜体 *...*
            html = html.replace(/\\*([^*]+)\\*/g, '<em>$1</em>');
            return html;
        }

        // 解析 AI 消息内容，分层显示
        // 新格式：JSON 数组 [{"type": "thinking/tool/text", "content": "...", "name": "..."}]
        // 旧格式：纯文本（向后兼容）
        function parseAssistantContent(content) {
            // 尝试解析为 JSON（新格式）
            try {
                const blocks = JSON.parse(content);
                if (Array.isArray(blocks) && blocks.length > 0) {
                    return blocks.map(b => ({
                        type: b.type === 'thinking' ? 'thinking' : (b.type === 'tool' ? 'tool' : 'text'),
                        content: b.content || '',
                        name: b.name || ''
                    }));
                }
            } catch (e) {
                // 不是 JSON，使用旧的文本解析方式（向后兼容）
            }

            // 旧格式：纯文本，返回单个文本块
            return [{ type: 'text', content: content }];
        }

        function formatModelName(model) {
            if (!model) return 'Assistant';
            return model;
        }

        function getInteractionsForMessage(msgTimestamp, prevMsgTimestamp) {
            // 找到在当前消息时间戳之前、上一条消息之后的 interactions
            const msgTime = new Date(msgTimestamp).getTime();
            const prevTime = prevMsgTimestamp ? new Date(prevMsgTimestamp).getTime() : 0;
            return interactionsData.filter(i => {
                const iTime = new Date(i.timestamp).getTime();
                return iTime <= msgTime && iTime > prevTime;
            });
        }

        function toggleInteractionPanel(id) {
            const el = document.getElementById(id);
            if (!el) return;
            if (el.style.display === 'none') {
                el.style.display = 'block';
                expandedInteractions.add(id);
            } else {
                el.style.display = 'none';
                expandedInteractions.delete(id);
            }
        }

        function renderInteractions(interactions, msgId) {
            if (!interactions || interactions.length === 0) return '';
            const summary = interactions.map(i => {
                const icon = i.type === 'permission_request' ? '🔐' : '❓';
                const response = i.user_response === 'yes' ? '✓' : (i.user_response === 'no' ? '✗' : i.user_response);
                return `${icon} ${i.tool_name}: ${response}`;
            }).join(' | ');

            let detailHtml = interactions.map(i => {
                const icon = i.type === 'permission_request' ? '🔐' : '❓';
                const typeLabel = i.type === 'permission_request' ? 'Permission' : 'Choice';
                const responseColor = i.user_response === 'yes' ? '#4ade80' : (i.user_response === 'no' ? '#f87171' : '#fbbf24');
                const content = i.request_content.length > 100 ? i.request_content.substring(0, 100) + '...' : i.request_content;
                return `<div style="padding: 4px 0; border-bottom: 1px solid #333;">
                    <span style="color: #888;">${icon} ${typeLabel}</span>
                    <span style="color: #d9a04a; margin-left: 8px;">${escapeHtml(i.tool_name)}</span>
                    <span style="color: ${responseColor}; margin-left: 8px; font-weight: bold;">${escapeHtml(i.user_response)}</span>
                    <div style="color: #777; font-size: 0.85em; margin-top: 2px; font-family: monospace;">${escapeHtml(content)}</div>
                </div>`;
            }).join('');

            // 使用稳定的 ID（基于消息 ID）
            const id = 'int-msg-' + msgId;
            const isExpanded = expandedInteractions.has(id);
            return `<div style="background: #1a1a2e; border-radius: 6px; padding: 6px 10px; margin-bottom: 8px; border-left: 3px solid #9333ea; font-size: 0.85em;">
                <div style="cursor: pointer; color: #a78bfa;" onclick="toggleInteractionPanel('${id}')">
                    ⚡ ${interactions.length} interaction${interactions.length > 1 ? 's' : ''}: ${summary.length > 60 ? summary.substring(0, 60) + '...' : summary}
                </div>
                <div id="${id}" style="display: ${isExpanded ? 'block' : 'none'}; margin-top: 6px;">${detailHtml}</div>
            </div>`;
        }

        function renderAssistantMessage(m, prevTimestamp) {
            const parts = parseAssistantContent(m.content);
            const modelLabel = formatModelName(m.model);
            const interactions = getInteractionsForMessage(m.timestamp, prevTimestamp);

            let html = `<div style="display: flex; justify-content: flex-start; margin-bottom: 20px;">
                <div style="max-width: 85%; width: 100%;">
                    <div style="font-size: 0.75em; color: #e94560; margin-bottom: 8px; font-weight: bold;">${modelLabel} <span style="color: #666; font-weight: normal;">#${m.id}</span></div>`;

            // 先显示 interactions（在消息内容之前）
            html += renderInteractions(interactions, m.id);

            for (const part of parts) {
                if (part.type === 'thinking') {
                    html += `<div style="background: #1a2a3a; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; border-left: 3px solid #4a90d9;">
                        <div style="font-size: 0.7em; color: #4a90d9; margin-bottom: 4px; font-weight: bold;">💭 Thinking</div>
                        <div style="white-space: pre-wrap; word-break: break-word; line-height: 1.4; color: #9ab; font-size: 0.9em;">${renderMarkdown(part.content)}</div>
                    </div>`;
                } else if (part.type === 'tool') {
                    const toolName = part.name || 'Tool';
                    html += `<div style="background: #2a2a1a; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; border-left: 3px solid #d9a04a;">
                        <div style="font-size: 0.7em; color: #d9a04a; margin-bottom: 4px; font-weight: bold;">🔧 ${escapeHtml(toolName)}</div>
                        <div style="white-space: pre-wrap; word-break: break-word; line-height: 1.4; color: #cb9; font-size: 0.85em; font-family: monospace;">${escapeHtml(part.content)}</div>
                    </div>`;
                } else {
                    if (part.content.trim()) {
                        html += `<div style="background: #2d1f3d; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; border-left: 3px solid #e94560;">
                            <div style="white-space: pre-wrap; word-break: break-word; line-height: 1.5; color: #eee;">${renderMarkdown(part.content)}</div>
                        </div>`;
                    }
                }
            }

            html += `<div style="font-size: 0.7em; color: #666; margin-top: 4px; text-align: right;">${m.timestamp} ${m.is_summarized ? '✓ Summarized' : ''}</div>
                </div>
            </div>`;
            return html;
        }

        async function loadMessages() {
            if (!currentProject) return;
            const msgUrl = currentSession ? `/api/projects/${currentProject}/messages?session_id=${encodeURIComponent(currentSession)}` : `/api/projects/${currentProject}/messages`;
            const intUrl = currentSession ? `/api/projects/${currentProject}/interactions?session_id=${encodeURIComponent(currentSession)}` : `/api/projects/${currentProject}/interactions`;

            const [msgRes, intRes] = await Promise.all([fetch(msgUrl), fetch(intUrl)]);
            const msgData = await msgRes.json();
            const intData = await intRes.json();

            const messages = [...(msgData.messages || [])].reverse();
            interactionsData = intData.interactions || [];
            const listEl = document.getElementById('message-list');
            listEl.innerHTML = messages.map((m, idx) => {
                const prevTimestamp = idx > 0 ? messages[idx - 1].timestamp : null;
                if (m.role === 'user') {
                    return `
                    <div style="display: flex; justify-content: flex-end; margin-bottom: 20px;">
                        <div style="max-width: 80%; background: #1a3a5c; border-radius: 12px; padding: 12px 16px; border-left: 3px solid #00d9ff;">
                            <div style="font-size: 0.75em; color: #00d9ff; margin-bottom: 6px; font-weight: bold;">You <span style="color: #666; font-weight: normal;">#${m.id}</span></div>
                            <div style="white-space: pre-wrap; word-break: break-word; line-height: 1.5; color: #eee;">${escapeHtml(m.content)}</div>
                            <div style="font-size: 0.7em; color: #666; margin-top: 8px; text-align: right;">${m.timestamp} ${m.is_summarized ? '✓ Summarized' : ''}</div>
                        </div>
                    </div>`;
                } else {
                    return renderAssistantMessage(m, prevTimestamp);
                }
            }).join('') || '<p style="color: #888; text-align: center; padding: 40px;">No messages found</p>';

            // 设置滚动监听（只设置一次）
            if (!listEl.hasAttribute('data-scroll-init')) {
                listEl.setAttribute('data-scroll-init', 'true');
                listEl.onscroll = updateScrollButtonVisibility;
            }

            // 只在首次加载时滚动到底部
            if (!messagesInitialized) {
                messagesInitialized = true;
                setTimeout(() => {
                    listEl.scrollTop = listEl.scrollHeight;
                    updateScrollButtonVisibility();
                }, 100);
            } else {
                // 非首次加载时也检查一下按钮状态
                setTimeout(updateScrollButtonVisibility, 50);
            }
        }

        function scrollMessagesToBottom() {
            const listEl = document.getElementById('message-list');
            if (!listEl) return;
            listEl.scrollTop = listEl.scrollHeight;
            updateScrollButtonVisibility();
        }

        let extraSelection = [];  // 手动选择的额外总结
        let summariesData = [];
        let isEditing = false;
        let selectionDirty = false;
        let defaultInjectCount = 5;

        async function loadSummaries() {
            if (!currentProject) return;
            const [summariesRes, selectionRes, configRes] = await Promise.all([
                fetch(`/api/projects/${currentProject}/summaries`),
                fetch(`/api/projects/${currentProject}/summaries/selection`),
                fetch('/api/config')
            ]);
            const data = await summariesRes.json();
            const selData = await selectionRes.json();
            const configData = await configRes.json();
            summariesData = data.summaries || [];
            defaultInjectCount = parseInt(configData.config?.inject_summary_count || configData.defaults?.inject_summary_count || 5);
            extraSelection = selData.selected_ids || [];
            renderSummaries();
        }

        let expandedSummaries = new Set();

        function renderSummaries() {
            const allEl = document.getElementById('summary-list-all');
            const autoEl = document.getElementById('summary-list-auto');
            const extraEl = document.getElementById('summary-list-extra');
            const countEl = document.getElementById('inject-count-display');
            if (!allEl || !autoEl || !extraEl) return;

            if (countEl) countEl.textContent = defaultInjectCount;

            if (summariesData.length === 0) {
                allEl.innerHTML = '<p style="color: #666;">No summaries</p>';
                autoEl.innerHTML = '<p style="color: #666;">No summaries</p>';
                extraEl.innerHTML = '<p style="color: #666;">None</p>';
                return;
            }

            const autoIds = new Set(summariesData.slice(0, defaultInjectCount).map(s => s.id));
            const extraSet = new Set(extraSelection);

            // 左栏：显示所有总结，已添加的变深色
            allEl.innerHTML = summariesData.map(s => {
                const isAuto = autoIds.has(s.id);
                const isExtra = extraSet.has(s.id);
                const isSelected = isAuto || isExtra;
                const bgColor = isSelected ? '#0a1525' : '#16213e';
                const borderColor = isAuto ? '#00d9ff' : (isExtra ? '#e94560' : '#333');
                const labelColor = isAuto ? '#00d9ff' : (isExtra ? '#e94560' : '#888');
                const badge = isAuto ? '<span style="background:#00d9ff;color:#000;padding:1px 4px;border-radius:3px;font-size:0.7em;margin-left:5px;">AUTO</span>' : (isExtra ? '<span style="background:#e94560;color:#fff;padding:1px 4px;border-radius:3px;font-size:0.7em;margin-left:5px;">EXTRA</span>' : '');
                const isExpanded = expandedSummaries.has(s.id);
                const hasRange = s.message_range_start && s.message_range_end;
                return `
                <div class="card" style="margin-bottom: 8px; padding: 8px; background: ${bgColor}; border-left: 3px solid ${borderColor};">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div style="display: flex; align-items: center; cursor: pointer;" onclick="toggleExpandSummary(${s.id})">
                            <span style="color: #666; margin-right: 5px;">${isExpanded ? '▼' : '▶'}</span>
                            <strong style="color: ${labelColor};">#${s.id}</strong>${badge}
                        </div>
                        <div style="display: flex; gap: 4px;">
                            ${!isAuto && !isExtra ? `<button onclick="event.stopPropagation();addExtra(${s.id})" style="padding: 2px 6px; font-size: 0.75em; background: #e94560;">+</button>` : ''}
                            ${isExtra ? `<button onclick="event.stopPropagation();removeExtra(${s.id})" style="padding: 2px 6px; font-size: 0.75em; background: #333;">✕</button>` : ''}
                            <button onclick="event.stopPropagation();toggleEditSummary(${s.id})" style="padding: 2px 6px; font-size: 0.75em;">Edit</button>
                            <button onclick="event.stopPropagation();regenerateSummary(${s.id})" style="padding: 2px 6px; font-size: 0.75em; background: #e94560;">Regen</button>
                            ${hasRange ? `<button onclick="event.stopPropagation();showSummaryMessages(${s.id}, ${s.message_range_start}, ${s.message_range_end})" style="padding: 2px 6px; font-size: 0.75em; background: #1f4068;">📜</button>` : ''}
                        </div>
                    </div>
                    <div style="font-size: 0.8em; color: #666; margin: 3px 0;">${s.created_at} | ${s.message_count} msgs</div>
                    <div id="summary-view-${s.id}" style="font-size: 0.85em; color: #ccc; margin-top: 5px; ${isExpanded ? '' : 'max-height: 50px; overflow: hidden;'}">${escapeHtml(s.summary_text)}</div>
                    <div id="summary-edit-${s.id}" style="display: none; margin-top: 8px;">
                        <textarea id="summary-textarea-${s.id}" style="width: 100%; min-height: 100px; background: #1a1a2e; color: #eee; border: 1px solid #333; border-radius: 5px; padding: 6px; font-size: 0.85em;">${escapeHtml(s.summary_text)}</textarea>
                        <div style="margin-top: 5px;">
                            <button onclick="saveSummaryEdit(${s.id})" style="padding: 3px 10px; font-size: 0.8em;">Save</button>
                            <button onclick="toggleEditSummary(${s.id})" style="padding: 3px 10px; font-size: 0.8em; background: #333;">Cancel</button>
                        </div>
                    </div>
                </div>`;
            }).join('');

            // 右栏 Auto 部分（旧到新）
            const autoSummaries = summariesData.slice(0, defaultInjectCount).reverse();
            autoEl.innerHTML = autoSummaries.map(s => `
                <div style="padding: 6px; margin-bottom: 4px; background: #0a1a2a; border-radius: 4px; font-size: 0.85em;">
                    <strong style="color: #00d9ff;">#${s.id}</strong>
                    <span style="color: #666; margin-left: 8px;">${s.summary_text.substring(0, 60)}...</span>
                </div>
            `).join('') || '<p style="color: #666;">None</p>';

            // 右栏 Extra 部分
            const extraSummaries = extraSelection.map(id => summariesData.find(s => s.id === id)).filter(Boolean);
            extraEl.innerHTML = extraSummaries.map(s => `
                <div class="card" data-id="${s.id}" draggable="true" ondragstart="onDragStart(event)" ondragover="onDragOver(event)" ondrop="onDrop(event)" ondragend="onDragEnd(event)" style="margin-bottom: 6px; padding: 6px; cursor: grab; border-left: 3px solid #e94560; background: #1a1a2e;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div style="display: flex; align-items: center; gap: 4px;">
                            <span style="color: #666;">⋮⋮</span>
                            <strong style="color: #e94560;">#${s.id}</strong>
                        </div>
                        <button onclick="removeExtra(${s.id})" style="padding: 2px 6px; font-size: 0.75em; background: #333;">✕</button>
                    </div>
                    <div style="font-size: 0.8em; color: #999; margin-top: 4px;">${s.summary_text.substring(0, 80)}...</div>
                </div>
            `).join('') || '<p style="color: #666;">Click + on left to add</p>';
        }

        function toggleExpandSummary(id) {
            if (expandedSummaries.has(id)) {
                expandedSummaries.delete(id);
            } else {
                expandedSummaries.add(id);
            }
            renderSummaries();
        }

        async function showSummaryMessages(summaryId, startId, endId) {
            const res = await fetch(`/api/projects/${currentProject}/messages/range?start=${startId}&end=${endId}`);
            const data = await res.json();
            const messages = data.messages || [];

            // 使用 processed_content（后端统一处理）计算哪些消息会被包含
            const maxTotal = appConfig.summary_max_chars_total;
            let totalChars = 0;
            let includedCount = 0;

            // 从后往前计算
            const reversed = [...messages].reverse();
            for (const m of reversed) {
                const contentLen = (m.processed_content || '').length;
                if (totalChars + contentLen > maxTotal) break;
                totalChars += contentLen;
                includedCount++;
            }
            const excludedCount = messages.length - includedCount;

            let html = '';
            if (excludedCount > 0) {
                html += `<div style="background: #4a3000; color: #ffcc00; padding: 10px; border-radius: 8px; margin-bottom: 15px; font-size: 0.85em;">
                    ⚠️ 前 ${excludedCount} 条消息因字符限制未包含在 summary 中（总限制 ${maxTotal} 字符）
                </div>`;
            }

            html += messages.map((m, idx) => {
                const isUser = m.role === 'user';
                const bgColor = isUser ? '#1a3a5c' : '#2d1f3d';
                const borderColor = isUser ? '#00d9ff' : '#e94560';
                const isExcluded = idx < excludedCount;
                // 使用 processed_content 作为显示内容
                const displayContent = m.processed_content || m.content.substring(0, 200) + '...';
                const isTruncated = m.processed_content && m.processed_content !== m.content;

                const excludedStyle = isExcluded ? 'opacity: 0.4;' : '';
                const excludedBadge = isExcluded ? '<span style="background: #666; color: #ccc; padding: 1px 4px; border-radius: 3px; font-size: 0.7em; margin-left: 5px;">未包含</span>' : '';
                const truncatedBadge = isTruncated && !isExcluded ? '<span style="background: #3a3a1a; color: #d9d94a; padding: 1px 4px; border-radius: 3px; font-size: 0.7em; margin-left: 5px;">已简化</span>' : '';

                const roleLabel = isUser ? 'You' : formatModelName(m.model);
                return `<div style="display: flex; justify-content: ${isUser ? 'flex-end' : 'flex-start'}; margin-bottom: 10px; ${excludedStyle}">
                    <div style="max-width: 90%; background: ${bgColor}; border-radius: 8px; padding: 10px; border-left: 3px solid ${borderColor};">
                        <div style="font-size: 0.7em; color: ${borderColor}; font-weight: bold;">${roleLabel} <span style="color: #666; font-weight: normal;">#${m.id}</span>${excludedBadge}${truncatedBadge}</div>
                        <div style="white-space: pre-wrap; word-break: break-word; font-size: 0.85em; color: #eee;">${escapeHtml(displayContent)}</div>
                    </div>
                </div>`;
            }).join('');

            document.getElementById('summary-messages-content').innerHTML = html || '<p style="color:#888;">No messages found</p>';
            document.getElementById('summary-messages-modal').style.display = 'block';
        }

        function closeSummaryMessagesModal() {
            document.getElementById('summary-messages-modal').style.display = 'none';
        }

        function addExtra(id) {
            if (!extraSelection.includes(id)) {
                extraSelection.push(id);
                selectionDirty = true;
                updateDirtyHint();
                renderSummaries();
            }
        }

        function removeExtra(id) {
            extraSelection = extraSelection.filter(x => x !== id);
            selectionDirty = true;
            updateDirtyHint();
            renderSummaries();
        }

        function updateDirtyHint() {
            const hint = document.getElementById('selection-dirty-hint');
            if (hint) hint.style.display = selectionDirty ? 'inline' : 'none';
        }

        function toggleEditSummary(id) {
            const viewEl = document.getElementById(`summary-view-${id}`);
            const editEl = document.getElementById(`summary-edit-${id}`);
            if (editEl.style.display === 'none') {
                viewEl.style.display = 'none';
                editEl.style.display = 'block';
                isEditing = true;
            } else {
                viewEl.style.display = 'block';
                editEl.style.display = 'none';
                isEditing = false;
            }
        }

        async function saveSummaryEdit(id) {
            const textarea = document.getElementById(`summary-textarea-${id}`);
            const newText = textarea.value;
            await fetch(`/api/projects/${currentProject}/summaries/${id}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({summary_text: newText})
            });
            const s = summariesData.find(x => x.id === id);
            if (s) s.summary_text = newText;
            isEditing = false;
            toggleEditSummary(id);
            renderSummaries();
        }

        async function regenerateSummary(id) {
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = '...';
            const res = await fetch(`/api/projects/${currentProject}/summaries/${id}/regenerate`, {method: 'POST'});
            const data = await res.json();
            btn.disabled = false;
            btn.textContent = 'Regen';
            if (data.error) {
                console.error('Regenerate error:', data.error);
                return;
            }
            const s = summariesData.find(x => x.id === id);
            if (s) s.summary_text = data.summary_text;
            renderSummaries();
        }

        async function triggerSummary() {
            if (!currentProject) return;
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Summarizing...';
            try {
                const res = await fetch(`/api/projects/${currentProject}/summaries/trigger`, {method: 'POST'});
                const data = await res.json();
                if (data.created) {
                    loadSummaries();
                } else {
                    alert(data.message || 'No summary created');
                }
            } catch (e) {
                console.error('Trigger summary error:', e);
            }
            btn.disabled = false;
            btn.textContent = 'Summarize Now';
        }

        async function regenerateAllSummaries() {
            if (!currentProject) return;
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Regenerating...';
            const res = await fetch(`/api/projects/${currentProject}/summaries/regenerate-all`, {method: 'POST'});
            const data = await res.json();
            btn.disabled = false;
            btn.textContent = 'Regenerate All';
            if (data.error) {
                console.error('Regenerate all error:', data.error);
                return;
            }
            loadSummaries();
        }

        async function saveSummarySelection() {
            if (!currentProject) return;
            await fetch(`/api/projects/${currentProject}/summaries/selection`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({selected_ids: extraSelection})
            });
            selectionDirty = false;
            updateDirtyHint();
        }

        let draggedId = null;
        function onDragStart(e) {
            draggedId = parseInt(e.target.dataset.id);
            e.dataTransfer.effectAllowed = 'move';
            isEditing = true;
        }
        function onDragOver(e) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
        }
        function onDrop(e) {
            e.preventDefault();
            isEditing = false;
            const targetEl = e.target.closest('.card[data-id]');
            if (!targetEl) return;
            const targetId = parseInt(targetEl.dataset.id);
            if (draggedId === targetId) return;
            // 只在 extra 列表内重新排序
            if (!extraSelection.includes(draggedId) || !extraSelection.includes(targetId)) return;
            const draggedIdx = extraSelection.indexOf(draggedId);
            const targetIdx = extraSelection.indexOf(targetId);
            extraSelection.splice(draggedIdx, 1);
            extraSelection.splice(targetIdx, 0, draggedId);
            selectionDirty = true;
            updateDirtyHint();
            renderSummaries();
        }
        function onDragEnd(e) {
            isEditing = false;
        }

        async function loadContext() {
            if (!currentProject) return;
            const res = await fetch(`/api/projects/${currentProject}/context`);
            const data = await res.json();
            let html = '<div class="context-preview">';
            if (data.summaries) {
                html += `<div class="context-section"><div class="context-label">Historical Summaries:</div><div class="summary-text">${escapeHtml(data.summaries)}</div></div>`;
            }
            // 显示累积知识（与 sessionStart.py 一致：全部 6 类）
            if (data.knowledge) {
                const k = data.knowledge;
                const catNames = (window.i18n && window.i18n.category_names) || {};
                let knowledgeHtml = '';
                const categories = ['user_preferences', 'project_decisions', 'key_facts', 'pending_tasks', 'learned_patterns', 'important_context'];
                for (const cat of categories) {
                    if (k[cat] && k[cat].length > 0) {
                        const label = catNames[cat] || cat;
                        knowledgeHtml += `<div style="margin: 5px 0;"><strong>${escapeHtml(label)}:</strong> ${escapeHtml(k[cat].join(', '))}</div>`;
                    }
                }
                if (knowledgeHtml) {
                    html += `<div class="context-section"><div class="context-label">Accumulated Knowledge:</div><div class="summary-text">${knowledgeHtml}</div></div>`;
                }
            }
            if (data.messages && data.messages.length > 0) {
                html += `<div class="context-section"><div class="context-label">Recent Messages (${data.messages.length}):</div>`;
                data.messages.forEach(m => {
                    html += `<div class="message ${m.role}"><div class="message-role">${m.role}</div><div class="message-content">${escapeHtml(m.content)}</div></div>`;
                });
                html += '</div>';
            }
            if (data.decisions && data.decisions.length > 0) {
                let decisionsHtml = '';
                data.decisions.forEach(d => {
                    let line = `<div style="margin: 5px 0;">- <strong>${escapeHtml(d.problem)}</strong> → ${escapeHtml(d.solution)}`;
                    if (d.reason) {
                        line += ` <span style="color: #888;">(因为: ${escapeHtml(d.reason)})</span>`;
                    }
                    line += '</div>';
                    decisionsHtml += line;
                });
                html += `<div class="context-section"><div class="context-label">Related Decisions (${data.decisions.length}):</div><div class="summary-text">${decisionsHtml}</div></div>`;
            }
            if (!data.summaries && (!data.messages || data.messages.length === 0) && !data.knowledge && (!data.decisions || data.decisions.length === 0)) {
                html += '<p style="color: #888;">No context available for this project yet.</p>';
            }
            html += '</div>';
            const el = document.getElementById('context-preview');
            el.innerHTML = html;
            el.scrollTop = el.scrollHeight;
        }

        function onSearchMethodChange() {
            const method = document.getElementById('search-method').value;
            document.getElementById('fuzzy-options').style.display = method === 'fuzzy' ? 'block' : 'none';
        }

        function onSearchTypeChange() {
            // 切换搜索类型时可以做一些 UI 调整
            const searchType = document.getElementById('search-type').value;
            document.getElementById('search-query').placeholder = searchType === 'decision'
                ? 'Search decisions (problem, solution, reason)...'
                : 'Enter search query...';
        }

        async function doUnifiedSearch() {
            const query = document.getElementById('search-query').value;
            if (!query) return;

            const searchType = document.getElementById('search-type').value;
            const scope = document.getElementById('search-scope').value;
            const method = document.getElementById('search-method').value;
            const threshold = document.getElementById('fuzzy-threshold').value || 60;

            if (scope === 'current' && !currentProject) {
                document.getElementById('search-results').innerHTML = '<div class="card" style="color: #888;">Please select a project first, or search all projects.</div>';
                return;
            }

            document.getElementById('search-status').textContent = 'Searching...';
            document.getElementById('search-results').innerHTML = '';

            // 根据搜索类型选择 API
            const apiPath = searchType === 'decision' ? '/api/search/decisions' : '/api/search';
            let url = `${apiPath}?query=${encodeURIComponent(query)}&method=${method}&scope=${scope}&limit=30`;
            if (scope === 'current') {
                url += `&project=${encodeURIComponent(currentProject)}`;
            }
            if (method === 'fuzzy') {
                url += `&threshold=${threshold}`;
            }

            try {
                const res = await fetch(url);
                const data = await res.json();

                if (data.error) {
                    document.getElementById('search-status').textContent = `Error: ${data.error}`;
                    return;
                }

                const typeLabel = searchType === 'decision' ? 'decisions' : 'messages';
                const methodLabels = {vector: '🧠 Semantic', bm25: '🔑 Keyword', fuzzy: '〰️ Fuzzy', combined: '🔗 Combined'};
                document.getElementById('search-status').textContent = `Found ${data.total || 0} ${typeLabel} using ${methodLabels[data.method] || data.method}`;

                if (searchType === 'decision') {
                    // Decision 结果渲染
                    document.getElementById('search-results').innerHTML = data.results.map(r => {
                        const methodBadge = r.method ? `<span style="background: ${r.method === 'vector' ? '#4a90d9' : r.method === 'bm25' ? '#d94a90' : '#6c5ce7'}; color: #fff; padding: 1px 6px; border-radius: 3px; font-size: 0.7em; margin-left: 5px;">${r.method}</span>` : '';
                        const scoreBadge = r.score > 0 ? `<span style="color: #888; font-size: 0.8em; margin-left: 8px;">score: ${r.score}</span>` : '';
                        const projectBadge = scope === 'all' ? `<span class="badge" style="margin-left: 5px;">${r.project}</span>` : '';
                        return `
                        <div class="card" style="margin-bottom: 10px; background: #1f2d3d; border-left: 3px solid #6c5ce7;">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                                <div>
                                    <span style="color: #6c5ce7; font-weight: bold;">Decision #${r.id}</span>
                                    ${projectBadge}${methodBadge}${scoreBadge}
                                </div>
                                <span style="color: #666; font-size: 0.75em;">${r.timestamp}</span>
                            </div>
                            <div style="margin-bottom: 8px;">
                                <span style="color: #e94560; font-weight: bold;">Problem:</span>
                                <span style="color: #eee;">${escapeHtml(r.problem)}</span>
                            </div>
                            <div style="margin-bottom: 8px;">
                                <span style="color: #00d9ff; font-weight: bold;">Solution:</span>
                                <span style="color: #eee;">${escapeHtml(r.solution)}</span>
                            </div>
                            ${r.reason ? `<div style="color: #888; font-size: 0.9em;"><span style="color: #d9a04a;">Reason:</span> ${escapeHtml(r.reason)}</div>` : ''}
                            ${r.note ? `<div style="color: #888; font-size: 0.9em;"><span style="color: #888;">Note:</span> ${escapeHtml(r.note)}</div>` : ''}
                        </div>`;
                    }).join('') || '<div class="card" style="color: #888;">No decisions found</div>';
                } else {
                    // Message 结果渲染
                    document.getElementById('search-results').innerHTML = data.results.map(r => {
                        const isUser = r.role === 'user';
                        const bgColor = isUser ? '#1a3a5c' : '#2d1f3d';
                        const borderColor = isUser ? '#00d9ff' : '#e94560';
                        const roleLabel = isUser ? 'user' : formatModelName(r.model);
                        const methodBadge = r.method ? `<span style="background: ${r.method === 'vector' ? '#4a90d9' : '#d94a90'}; color: #fff; padding: 1px 6px; border-radius: 3px; font-size: 0.7em; margin-left: 5px;">${r.method}</span>` : '';
                        const scoreBadge = r.score > 0 ? `<span style="color: #888; font-size: 0.8em; margin-left: 8px;">score: ${r.score}</span>` : '';
                        const projectBadge = scope === 'all' ? `<span class="badge" style="margin-left: 5px;">${r.project}</span>` : '';
                        return `
                        <div class="card" style="margin-bottom: 10px; background: ${bgColor}; border-left: 3px solid ${borderColor};">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                                <div>
                                    <span style="color: ${borderColor}; font-weight: bold;">${roleLabel}</span>
                                    ${projectBadge}${methodBadge}${scoreBadge}
                                </div>
                                <span style="color: #666; font-size: 0.75em;">${r.timestamp}</span>
                            </div>
                            <div style="white-space: pre-wrap; word-break: break-word; line-height: 1.5; color: #eee;">${escapeHtml(r.content)}</div>
                        </div>`;
                    }).join('') || '<div class="card" style="color: #888;">No results found</div>';
                }
            } catch (e) {
                document.getElementById('search-status').textContent = `Error: ${e.message}`;
            }
        }

        // Legacy search for backward compatibility
        async function doSearch() {
            doUnifiedSearch();
        }

        let logEventSource = null;
        let logLineCount = 0;
        const MAX_LOG_LINES = 1000;

        function formatLogLine(log) {
            const line = log.line || log;
            const source = log.source || 'app';
            let cls = 'log-line';
            let levelMatch = line.match(/\\| (ERROR|WARNING|INFO|DEBUG|CRITICAL)\\s*\\|/i);
            if (levelMatch) {
                cls += ' ' + levelMatch[1].toLowerCase();
            } else if (line.includes('ERROR') || line.includes('error')) {
                cls += ' error';
            } else if (line.includes('WARNING') || line.includes('warning')) {
                cls += ' warning';
            } else if (line.includes('INFO')) {
                cls += ' info';
            } else if (line.includes('DEBUG')) {
                cls += ' debug';
            }

            const sourceTag = `<span class="log-source">[${source}]</span> `;
            return `<div class="${cls}" data-level="${cls.split(' ')[1] || 'other'}" data-source="${source}">${sourceTag}${escapeHtml(line)}</div>`;
        }

        async function loadLogs() {
            const source = document.getElementById('log-source').value;
            const res = await fetch(`/api/logs?lines=300&source=${source}`);
            const data = await res.json();
            const el = document.getElementById('log-list');
            el.innerHTML = data.logs.map(log => formatLogLine(log)).join('');
            logLineCount = data.logs.length;
            filterLogLevel();
            if (document.getElementById('log-autoscroll').checked) {
                el.scrollTop = el.scrollHeight;
            }
            updateLogStatus(`Loaded ${data.logs.length} lines`);
        }

        function appendLogLine(log) {
            const el = document.getElementById('log-list');
            el.insertAdjacentHTML('beforeend', formatLogLine(log));
            logLineCount++;

            // 限制最大行数
            if (logLineCount > MAX_LOG_LINES) {
                const firstChild = el.firstElementChild;
                if (firstChild) {
                    firstChild.remove();
                    logLineCount--;
                }
            }

            filterLogLevel();
            if (document.getElementById('log-autoscroll').checked) {
                el.scrollTop = el.scrollHeight;
            }
        }

        function filterLogLevel() {
            const level = document.getElementById('log-level').value;
            const lines = document.querySelectorAll('#log-list .log-line');
            lines.forEach(line => {
                if (level === 'all') {
                    line.classList.remove('hidden');
                } else {
                    const lineLevel = line.dataset.level;
                    line.classList.toggle('hidden', lineLevel !== level && lineLevel !== 'other');
                }
            });
        }

        function clearLogDisplay() {
            document.getElementById('log-list').innerHTML = '';
            logLineCount = 0;
            updateLogStatus('Cleared');
        }

        function updateLogStatus(msg) {
            const el = document.getElementById('log-status');
            el.textContent = `${new Date().toLocaleTimeString()} - ${msg}`;
        }

        function toggleRealtimeLogs() {
            const enabled = document.getElementById('log-realtime').checked;
            if (enabled) {
                startRealtimeLogs();
            } else {
                stopRealtimeLogs();
            }
        }

        function startRealtimeLogs() {
            if (logEventSource) {
                logEventSource.close();
            }
            updateLogStatus('Connecting to realtime stream...');
            logEventSource = new EventSource('/api/logs/stream');

            logEventSource.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'connected') {
                    updateLogStatus('Realtime: Connected');
                } else if (data.type === 'log') {
                    appendLogLine(data.data);
                }
            };

            logEventSource.onerror = () => {
                updateLogStatus('Realtime: Disconnected (retrying...)');
            };
        }

        function stopRealtimeLogs() {
            if (logEventSource) {
                logEventSource.close();
                logEventSource = null;
                updateLogStatus('Realtime: Stopped');
            }
        }

        let configMeta = {};
        let configDefaults = {};

        function openConfigModal() {
            document.getElementById('config-modal').style.display = 'block';
            loadConfig();
        }

        function closeConfigModal() {
            document.getElementById('config-modal').style.display = 'none';
        }

        async function loadConfig() {
            const res = await fetch('/api/config');
            const data = await res.json();
            configMeta = data.meta || {};
            configDefaults = data.defaults || {};
            const config = data.config || {};
            const defaultPrompts = data.default_prompts || {};
            // 存储 i18n 数据供全局使用
            window.i18n = data.i18n || {
                category_names: {},
                role_labels: {},
                ui_text: {}
            };

            const groups = {
                'Memory': {
                    icon: '🧠',
                    keys: ['short_term_window_size', 'max_context_tokens', 'summary_trigger_threshold']
                },
                'LLM (Ollama)': {
                    icon: '🤖',
                    keys: ['llm_provider', 'ollama_model', 'ollama_base_url', 'ollama_timeout', 'ollama_keep_alive', 'anthropic_model']
                },
                'Embedding': {
                    icon: '🔢',
                    keys: ['embedding_model', 'embedding_base_url', 'enable_vector_search']
                },
                'Search': {
                    icon: '🔍',
                    keys: ['search_result_preview_length']
                },
                'Knowledge': {
                    icon: '📚',
                    keys: ['enable_knowledge_extraction', 'knowledge_max_items_per_category']
                },
                'Content': {
                    icon: '📄',
                    keys: ['content_include_thinking', 'content_include_tool', 'content_include_text', 'content_max_chars_thinking', 'content_max_chars_tool', 'content_max_chars_text']
                },
                'Inject': {
                    icon: '💉',
                    keys: ['inject_summary_count', 'inject_recent_count', 'inject_knowledge_count', 'inject_task_count', 'inject_decision_count']
                },
                'Summary': {
                    icon: '📝',
                    keys: ['summary_max_chars_total']
                },
                'Stats': {
                    icon: '📊',
                    keys: ['input_token_price', 'output_token_price']
                },
                'Dashboard': {
                    icon: '🖥️',
                    keys: ['dashboard_refresh_interval']
                },
                'Prompts': {
                    icon: '✏️',
                    keys: ['prompt_language', 'summary_prompt_template', 'knowledge_extraction_prompt', 'decision_extraction_prompt']
                }
            };

            function renderConfigItem(key) {
                const meta = configMeta[key] || {label: key, description: '', type: 'text'};
                const value = config[key] || configDefaults[key] || '';
                const tooltip = meta.tooltip || '';
                const tooltipHtml = tooltip ? `<span class="tooltip-icon" data-tooltip="${escapeHtml(tooltip)}">?</span>` : '';
                const defaultPrompt = defaultPrompts[key] || '';

                let itemHtml = `<div class="config-item" style="margin-bottom: 16px; padding: 12px; background: #0a1929; border-radius: 8px;">`;
                itemHtml += `<label style="display: flex; align-items: center; gap: 8px; color: #00d9ff; font-weight: bold; margin-bottom: 5px;">
                    <span>${meta.label || key}</span>${tooltipHtml}
                </label>`;
                itemHtml += `<div style="color: #888; font-size: 0.85em; margin-bottom: 8px;">${meta.description || ''}</div>`;
                if (meta.type === 'select' && meta.options) {
                    itemHtml += `<select id="config-${key}" style="width: 100%; max-width: 400px; padding: 10px; background: #1a3a5c; color: #eee; border: 1px solid #333; border-radius: 5px;">`;
                    for (const opt of meta.options) {
                        const optValue = typeof opt === 'object' ? opt.value : opt;
                        const optLabel = typeof opt === 'object' ? opt.label : opt;
                        itemHtml += `<option value="${optValue}" ${value === optValue ? 'selected' : ''}>${optLabel}</option>`;
                    }
                    itemHtml += `</select>`;
                } else if (meta.type === 'number') {
                    itemHtml += `<input type="number" id="config-${key}" value="${value}" min="${meta.min || 0}" max="${meta.max || 99999}" style="width: 100%; max-width: 400px; padding: 10px; background: #1a3a5c; color: #eee; border: 1px solid #333; border-radius: 5px;">`;
                } else if (meta.type === 'textarea') {
                    const placeholder = defaultPrompt ? escapeHtml(defaultPrompt) : '';
                    const displayValue = value || '';
                    itemHtml += `<textarea id="config-${key}" placeholder="${placeholder}" style="width: 100%; min-height: 150px; padding: 10px; background: #1a3a5c; color: #eee; border: 1px solid #333; border-radius: 5px; font-family: monospace; font-size: 12px;">${escapeHtml(displayValue)}</textarea>`;
                    if (defaultPrompt) {
                        itemHtml += `<div style="margin-top: 8px;"><button type="button" onclick="document.getElementById('config-${key}').value = defaultPrompts['${key}']" style="padding: 4px 10px; background: #2a4a6a; color: #ccc; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Show Default</button></div>`;
                    }
                } else {
                    itemHtml += `<input type="text" id="config-${key}" value="${value}" style="width: 100%; max-width: 400px; padding: 10px; background: #1a3a5c; color: #eee; border: 1px solid #333; border-radius: 5px;">`;
                }
                itemHtml += `</div>`;
                return itemHtml;
            }

            let html = '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px;">';
            for (const [groupName, groupData] of Object.entries(groups)) {
                html += `<div class="config-group" style="background: #0f2847; border-radius: 10px; padding: 16px; border: 1px solid #1a3a5c;">`;
                html += `<div style="display: flex; align-items: center; gap: 10px; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid #1a3a5c;">
                    <span style="font-size: 1.5em;">${groupData.icon}</span>
                    <h3 style="margin: 0; color: #00d9ff;">${groupName}</h3>
                </div>`;
                for (const key of groupData.keys) {
                    html += renderConfigItem(key);
                }
                html += `</div>`;
            }
            html += '</div>';
            // 存储 defaultPrompts 供按钮使用
            window.defaultPrompts = defaultPrompts;
            document.getElementById('config-form').innerHTML = html;
        }

        async function saveAllConfig() {
            const configKeys = [
                'short_term_window_size', 'max_context_tokens', 'summary_trigger_threshold',
                'llm_provider', 'ollama_model', 'ollama_base_url', 'ollama_timeout', 'ollama_keep_alive',
                'anthropic_model', 'embedding_model', 'embedding_base_url', 'enable_vector_search', 'enable_knowledge_extraction',
                'input_token_price', 'output_token_price',
                'inject_summary_count', 'inject_recent_count', 'inject_knowledge_count', 'inject_task_count', 'inject_decision_count',
                'summary_max_chars_total',
                'content_include_thinking', 'content_include_tool', 'content_include_text',
                'content_max_chars_thinking', 'content_max_chars_tool', 'content_max_chars_text',
                'knowledge_max_items_per_category',
                'search_result_preview_length', 'dashboard_refresh_interval',
                'prompt_language', 'summary_prompt_template', 'knowledge_extraction_prompt', 'decision_extraction_prompt'
            ];
            for (const key of configKeys) {
                const el = document.getElementById(`config-${key}`);
                if (el) {
                    await fetch('/api/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key, value: el.value})});
                }
            }
            alert('Settings saved!');
            closeConfigModal();
        }

        async function resetConfig() {
            if (!confirm('Reset all settings to defaults?')) return;
            for (const [key, value] of Object.entries(configDefaults)) {
                await fetch('/api/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key, value})});
            }
            loadConfig();
        }

        function escapeHtml(text) {
            if (!text) return '';
            return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        // ========== Common UI Utilities ==========
        function formatDateTime(timestamp) {
            const date = new Date(timestamp);
            return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        }

        function renderHistoryItem(opts) {
            // opts: { id, timestamp, isSelected, borderColor, onClick, headerExtra, lines: [{text, color, size, ellipsis}] }
            const bg = opts.isSelected ? '#2a4a6a' : '#1a1a2e';
            const border = opts.isSelected ? '#00d9ff' : (opts.borderColor || '#333');
            const linesHtml = (opts.lines || []).map(l =>
                `<div style="font-size: ${l.size || '0.8em'}; color: ${l.color || '#888'}; margin-top: 3px; ${l.ellipsis ? 'overflow: hidden; text-overflow: ellipsis; white-space: nowrap;' : ''}">${escapeHtml(l.text)}</div>`
            ).join('');
            return `<div onclick="${opts.onClick}" style="padding: 8px; margin-bottom: 5px; background: ${bg}; border-radius: 6px; cursor: pointer; border-left: 3px solid ${border};">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 0.8em; color: #00d9ff;">#${opts.id}</span>
                    ${opts.headerExtra || ''}
                </div>
                <div style="font-size: 0.75em; color: #888;">${formatDateTime(opts.timestamp)}</div>
                ${linesHtml}
            </div>`;
        }

        function toggleSummaryDebug() {
            const panel = document.getElementById('summary-debug-panel');
            if (panel.style.display === 'none') {
                panel.style.display = 'block';
                loadSummaryDebug();
            } else {
                panel.style.display = 'none';
            }
        }

        async function loadSummaryDebug() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const res = await fetch(`/api/projects/${currentProject}/summary-debug`);
            const data = await res.json();

            // 显示更详细的状态信息
            const statusColor = data.pending_count > 0 ? '#ffcc00' : '#4caf50';
            document.getElementById('debug-info').innerHTML = `
                <div style="display: flex; flex-wrap: wrap; gap: 15px; align-items: center;">
                    <div><strong>Latest Msg:</strong> <span style="color: #00d9ff;">#${data.latest_message_id}</span></div>
                    <div><strong>Last Summary:</strong> <span style="color: #4caf50;">→ #${data.last_summary_end_id}</span></div>
                    <div><strong>Pending:</strong> <span style="color: ${statusColor}; font-weight: bold;">${data.pending_count} messages</span></div>
                    <div><strong>Unsummarized:</strong> ${data.message_count}</div>
                    <div><strong>Custom Template:</strong> ${data.using_custom_template ? 'Yes' : 'No'}</div>
                </div>
            `;
            document.getElementById('debug-msg-count').textContent = data.message_count;
            document.getElementById('debug-messages').innerHTML = data.messages.map(m => `
                <div style="margin: 3px 0; padding: 6px; border-radius: 4px; background: ${m.role === 'user' ? '#1a3a4a' : '#2a1a4a'}; font-size: 0.85em;">
                    <span style="color: ${m.role === 'user' ? '#00d9ff' : '#ff6b9d'}; font-weight: bold;">${m.role}</span>
                    <span style="color: #888; margin-left: 5px;">#${m.id}</span>
                    <span style="color: #666; margin-left: 5px; font-size: 0.8em;">[${m.session_id ? m.session_id.substring(0, 8) : 'unknown'}...]</span>
                    <div style="color: #ccc; margin-top: 3px;">${escapeHtml(m.content.substring(0, 150))}${m.content.length > 150 ? '...' : ''}</div>
                </div>
            `).join('') || '<p style="color: #888;">No unsummarized messages (all caught up!)</p>';
            document.getElementById('debug-prompt').textContent = data.full_prompt || 'No prompt generated';
        }

        async function loadVectorStats() {
            if (!currentProject) {
                document.getElementById('vector-stats').textContent = 'Select a project first';
                return;
            }
            try {
                const res = await fetch(`/api/projects/${currentProject}/vectors/stats`);
                const data = await res.json();
                if (data.error) {
                    document.getElementById('vector-stats').innerHTML = `<span style="color: #e94560;">Error: ${data.error}</span>`;
                } else {
                    const msgStats = data.message || {};
                    const decStats = data.decision || {};
                    document.getElementById('vector-stats').innerHTML = `
                        Messages: <strong>${msgStats.total_vectors || 0}</strong> |
                        Decisions: <strong>${decStats.total_vectors || 0}</strong> |
                        Dim: ${msgStats.dimension || 768}`;
                }
            } catch (e) {
                document.getElementById('vector-stats').innerHTML = `<span style="color: #e94560;">Failed to load</span>`;
            }
        }

        async function rebuildVectors() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            if (!confirm('This will clear and rebuild all MESSAGE vector embeddings.\\nThis may take a while for large projects.\\n\\nContinue?')) {
                return;
            }
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Rebuilding...';
            document.getElementById('vector-stats').innerHTML = '<span style="color: #d9a04a;">Rebuilding messages...</span>';

            try {
                const res = await fetch(`/api/projects/${currentProject}/vectors/rebuild`, {method: 'POST'});
                const data = await res.json();
                btn.disabled = false;
                btn.textContent = 'Rebuild Messages';

                if (data.error) {
                    alert('Error: ' + data.error);
                    loadVectorStats();
                } else {
                    document.getElementById('vector-stats').innerHTML = `<span style="color: #4ad9a0;">✓ Rebuilt ${data.rebuilt}/${data.total_messages} message vectors</span>`;
                    setTimeout(loadVectorStats, 2000);
                }
            } catch (e) {
                btn.disabled = false;
                btn.textContent = 'Rebuild Messages';
                alert('Failed to rebuild: ' + e.message);
                loadVectorStats();
            }
        }

        async function rebuildDecisionVectors() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            if (!confirm('This will clear and rebuild all DECISION vector embeddings.\\n\\nContinue?')) {
                return;
            }
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Rebuilding...';
            document.getElementById('vector-stats').innerHTML = '<span style="color: #d9a04a;">Rebuilding decisions...</span>';

            try {
                const res = await fetch(`/api/projects/${currentProject}/vectors/rebuild-decisions`, {method: 'POST'});
                const data = await res.json();
                btn.disabled = false;
                btn.textContent = 'Rebuild Decisions';

                if (data.error) {
                    alert('Error: ' + data.error);
                    loadVectorStats();
                } else {
                    document.getElementById('vector-stats').innerHTML = `<span style="color: #4ad9a0;">✓ Rebuilt ${data.rebuilt}/${data.total_decisions} decision vectors</span>`;
                    setTimeout(loadVectorStats, 2000);
                }
            } catch (e) {
                btn.disabled = false;
                btn.textContent = 'Rebuild Decisions';
                alert('Failed to rebuild: ' + e.message);
                loadVectorStats();
            }
        }

        async function refreshAll() {
            // 某些页面不自动刷新
            if (currentPanel === 'knowledge' || currentPanel === 'search') {
                return;
            }
            // 编辑中不刷新，避免打断用户操作
            if (isEditing || selectionDirty) {
                return;
            }
            await loadProjects();
            loadProjectData();
        }


        let knowledgeHistory = [];
        let selectedKnowledgeHistoryId = null;

        async function loadKnowledge() {
            if (!currentProject) {
                document.getElementById('knowledge-status').textContent = 'Select a project first';
                return;
            }
            document.getElementById('knowledge-status').textContent = 'Loading...';
            const res = await fetch(`/api/projects/${currentProject}/knowledge`);
            let data = await res.json();
            document.getElementById('knowledge-status').textContent = '';

            // 显示 pending 信息
            const statusColor = data.pending_count > 0 ? '#ffcc00' : '#4caf50';
            document.getElementById('knowledge-pending-info').innerHTML = `
                <div style="display: flex; flex-wrap: wrap; gap: 15px; align-items: center;">
                    <div><strong>Latest Msg:</strong> <span style="color: #00d9ff;">#${data.latest_message_id || 0}</span></div>
                    <div><strong>Last Extracted:</strong> <span style="color: #4caf50;">→ #${data.last_knowledge_end_id || 0}</span></div>
                    <div><strong>Pending:</strong> <span style="color: ${statusColor}; font-weight: bold;">${data.pending_count || 0} messages</span></div>
                </div>
            `;

            selectedKnowledgeHistoryId = null;
            document.getElementById('knowledge-viewing-label').textContent = 'Current';
            renderKnowledgeContent(data.knowledge || {}, data.max_per_category || 10);
            loadKnowledgeHistory();
        }

        function renderKnowledgeContent(k, maxPerCategory) {
            const categories = ['user-preferences', 'project-decisions', 'key-facts', 'pending-tasks', 'learned-patterns', 'important-context'];
            const keys = ['user_preferences', 'project_decisions', 'key_facts', 'pending_tasks', 'learned_patterns', 'important_context'];
            for (let i = 0; i < categories.length; i++) {
                const items = k[keys[i]] || [];
                const badge = ` (${items.length}/${maxPerCategory})`;
                document.getElementById(`k-${categories[i]}`).innerHTML = items.length > 0
                    ? `<div style="color: #888; margin-bottom: 5px; font-size: 0.85em;">${badge}</div>` + items.map(item => `<li style="margin: 5px 0; color: #ccc;">${escapeHtml(item)}</li>`).join('')
                    : '<li style="color: #666;">No items</li>';
            }
        }

        async function loadKnowledgeHistory() {
            if (!currentProject) return;
            try {
                const res = await fetch(`/api/projects/${currentProject}/knowledge/history`);
                const data = await res.json();
                knowledgeHistory = data.history || [];
                renderKnowledgeHistory();
            } catch (e) {
                console.error('Failed to load knowledge history:', e);
            }
        }

        function renderKnowledgeHistory() {
            const container = document.getElementById('knowledge-history-list');
            if (knowledgeHistory.length === 0) {
                container.innerHTML = '<div style="color: #666; font-size: 0.85em;">No history yet</div>';
                return;
            }
            container.innerHTML = knowledgeHistory.map(h => {
                const totalItems = Object.values(h.content || {}).reduce((sum, arr) => sum + (arr?.length || 0), 0);
                const isSelected = selectedKnowledgeHistoryId === h.id;
                const bg = isSelected ? '#2a4a6a' : '#1a1a2e';
                const border = isSelected ? '#00d9ff' : '#333';
                const hasRange = h.message_range_start && h.message_range_end;
                const msgBtn = hasRange ? `<button onclick="event.stopPropagation();showSummaryMessages(0, ${h.message_range_start}, ${h.message_range_end})" style="padding: 1px 4px; font-size: 0.7em; background: #1f4068;" title="View source messages">📜</button>` : '';
                return `<div onclick="viewKnowledgeHistory(${h.id})" style="padding: 8px; margin-bottom: 5px; background: ${bg}; border-radius: 6px; cursor: pointer; border-left: 3px solid ${border};">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-size: 0.8em; color: #00d9ff;">#${h.id}</span>
                        ${msgBtn}
                    </div>
                    <div style="font-size: 0.75em; color: #888;">${formatDateTime(h.created_at)}</div>
                    <div style="font-size: 0.75em; color: #666; margin-top: 3px;">${totalItems} items${h.message_count ? ` · ${h.message_count} msgs` : ''}</div>
                </div>`;
            }).join('');
        }

        let editingKnowledgeHistoryId = null;

        function viewKnowledgeHistory(id) {
            const h = knowledgeHistory.find(x => x.id === id);
            if (!h) return;
            selectedKnowledgeHistoryId = id;
            editingKnowledgeHistoryId = null;
            const date = new Date(h.created_at);
            document.getElementById('knowledge-viewing-label').innerHTML = `Viewing #${id} (${date.toLocaleDateString()}) <button onclick="editKnowledgeHistory(${id})" style="margin-left: 10px; padding: 2px 8px; font-size: 0.8em;">Edit</button>`;
            renderKnowledgeContent(h.content || {}, 999);
            renderKnowledgeHistory();
        }

        function editKnowledgeHistory(id) {
            const h = knowledgeHistory.find(x => x.id === id);
            if (!h) return;
            editingKnowledgeHistoryId = id;
            const date = new Date(h.created_at);
            document.getElementById('knowledge-viewing-label').innerHTML = `Editing #${id} (${date.toLocaleDateString()}) <button onclick="saveKnowledgeHistory(${id})" style="margin-left: 10px; padding: 2px 8px; font-size: 0.8em; background: #4ad9a0;">Save</button> <button onclick="viewKnowledgeHistory(${id})" style="padding: 2px 8px; font-size: 0.8em; background: #333;">Cancel</button>`;
            renderKnowledgeContentEditable(h.content || {});
        }

        function renderKnowledgeContentEditable(k) {
            const categories = ['user-preferences', 'project-decisions', 'key-facts', 'pending-tasks', 'learned-patterns', 'important-context'];
            const keys = ['user_preferences', 'project_decisions', 'key_facts', 'pending_tasks', 'learned_patterns', 'important_context'];
            for (let i = 0; i < categories.length; i++) {
                const items = k[keys[i]] || [];
                const textarea = `<textarea id="k-edit-${keys[i]}" style="width: 100%; min-height: 80px; background: #1a1a2e; color: #eee; border: 1px solid #333; border-radius: 5px; padding: 6px; font-size: 0.85em;">${items.join('\\n')}</textarea>`;
                document.getElementById(`k-${categories[i]}`).innerHTML = textarea;
            }
        }

        async function saveKnowledgeHistory(id) {
            const keys = ['user_preferences', 'project_decisions', 'key_facts', 'pending_tasks', 'learned_patterns', 'important_context'];
            const content = {};
            for (const key of keys) {
                const textarea = document.getElementById(`k-edit-${key}`);
                if (textarea) {
                    content[key] = textarea.value.split('\\n').map(s => s.trim()).filter(s => s);
                }
            }
            try {
                await fetch(`/api/projects/${currentProject}/knowledge/history/${id}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({content})
                });
                // Update local cache
                const h = knowledgeHistory.find(x => x.id === id);
                if (h) h.content = content;
                viewKnowledgeHistory(id);
                document.getElementById('knowledge-status').textContent = 'Saved';
                setTimeout(() => document.getElementById('knowledge-status').textContent = '', 2000);
            } catch (e) {
                document.getElementById('knowledge-status').textContent = 'Error: ' + e.message;
            }
        }

        async function extractKnowledge() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Extracting...';
            document.getElementById('knowledge-status').textContent = 'Sending to LLM...';
            const res = await fetch(`/api/projects/${currentProject}/knowledge/extract`, {method: 'POST'});
            const data = await res.json();
            btn.disabled = false;
            btn.textContent = 'Extract New';
            document.getElementById('knowledge-status').textContent = '';
            if (data.error) {
                alert('Error: ' + data.error);
                return;
            }
            const newItems = Object.values(data.extracted || {}).flat().length;
            document.getElementById('knowledge-status').textContent = `Extracted ${newItems} new items`;
            setTimeout(() => { document.getElementById('knowledge-status').textContent = ''; }, 3000);
            loadKnowledge();
        }

        async function loadKnowledgeDebug() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const res = await fetch(`/api/projects/${currentProject}/knowledge-debug`);
            const data = await res.json();

            const cfg = data.content_config || {};
            const statusColor = data.pending_count > 0 ? '#ffcc00' : '#4caf50';
            document.getElementById('knowledge-debug-info').innerHTML = `
                <div style="display: flex; flex-wrap: wrap; gap: 15px; align-items: center; margin-bottom: 8px;">
                    <div><strong>Latest Msg:</strong> <span style="color: #00d9ff;">#${data.latest_message_id || 0}</span></div>
                    <div><strong>Last Extracted:</strong> <span style="color: #4caf50;">→ #${data.last_knowledge_end_id || 0}</span></div>
                    <div><strong>Pending:</strong> <span style="color: ${statusColor}; font-weight: bold;">${data.pending_count || 0} messages</span></div>
                </div>
                <div style="font-size: 0.85em; color: #888;">Content: thinking=${cfg.include_thinking ? 'on' : 'off'} (${cfg.max_chars_thinking}), tool=${cfg.include_tool ? 'on' : 'off'} (${cfg.max_chars_tool}), text=${cfg.include_text ? 'on' : 'off'} (${cfg.max_chars_text})</div>
            `;

            document.getElementById('knowledge-debug-messages').innerHTML = data.messages.map(m => `
                <div style="padding: 5px; margin: 3px 0; background: ${m.role === 'user' ? '#1a3a5c' : '#2d1f3d'}; border-radius: 4px; font-size: 0.85em;">
                    <span style="color: ${m.role === 'user' ? '#00d9ff' : '#e94560'}; font-weight: bold;">${m.role}</span>
                    <span style="color: #888; margin-left: 8px;">${escapeHtml(m.content.substring(0, 100))}...</span>
                </div>
            `).join('') || '<div style="color: #888;">No messages</div>';

            const noKnowledgeText = (window.i18n && window.i18n.ui_text && window.i18n.ui_text.no_existing_knowledge) || '(No existing knowledge)';
            document.getElementById('knowledge-debug-existing').textContent = data.existing_knowledge || noKnowledgeText;
            document.getElementById('knowledge-debug-prompt').textContent = data.full_prompt || 'No prompt';
            document.getElementById('knowledge-debug-panel').style.display = 'block';
        }

        // ========== Decisions Functions ==========
        let allDecisions = [];
        let selectedDecisionId = null;
        let decisionExtraSelection = [];
        let decisionSelectionDirty = false;
        let defaultDecisionInjectCount = 5;

        async function loadDecisions() {
            if (!currentProject) return;
            document.getElementById('decisions-status').textContent = 'Loading...';

            try {
                // 获取所有 decisions（不过滤）
                const [decisionsRes, selRes, configRes] = await Promise.all([
                    fetch(`/api/projects/${currentProject}/decisions?status=`),
                    fetch(`/api/projects/${currentProject}/decisions/selection`),
                    fetch('/api/config')
                ]);
                const data = await decisionsRes.json();
                const selData = await selRes.json();
                const configData = await configRes.json();

                allDecisions = data.decisions || [];
                defaultDecisionInjectCount = parseInt(configData.config?.inject_decision_count || configData.defaults?.inject_decision_count || 5);
                decisionExtraSelection = selData.selected_ids || [];

                // 显示消息级别的 pending 信息
                const statusColor = data.pending_message_count > 0 ? '#ffcc00' : '#4caf50';
                document.getElementById('decision-pending-info').innerHTML = `
                    <div style="display: flex; flex-wrap: wrap; gap: 15px; align-items: center;">
                        <div><strong>Latest Msg:</strong> <span style="color: #00d9ff;">#${data.latest_message_id || 0}</span></div>
                        <div><strong>Last Extracted:</strong> <span style="color: #4caf50;">→ #${data.last_decision_end_id || 0}</span></div>
                        <div><strong>Pending:</strong> <span style="color: ${statusColor}; font-weight: bold;">${data.pending_message_count || 0} messages</span></div>
                        <div><strong>Pending Decisions:</strong> <span style="color: #e94560;">${data.pending_count || 0}</span></div>
                    </div>
                `;

                // Update pending badge
                const badge = document.getElementById('pending-decisions-badge');
                if (data.pending_count > 0) {
                    badge.textContent = data.pending_count;
                    badge.style.display = 'inline';
                } else {
                    badge.style.display = 'none';
                }

                document.getElementById('pending-count-label').textContent = `(${data.pending_count})`;

                // Render pending decisions
                const pending = allDecisions.filter(d => d.status === 'pending');
                document.getElementById('pending-decisions-list').innerHTML = pending.length > 0
                    ? pending.map(d => renderPendingDecision(d)).join('')
                    : '<div style="color: #888; padding: 10px;">No pending decisions</div>';

                // Render confirmed decisions with selection UI
                renderDecisions();

                document.getElementById('decisions-status').textContent = '';
            } catch (e) {
                document.getElementById('decisions-status').textContent = 'Error: ' + e.message;
            }
        }

        function renderDecisions() {
            const allEl = document.getElementById('decision-list-all');
            const autoEl = document.getElementById('decision-list-auto');
            const extraEl = document.getElementById('decision-list-extra');
            const countEl = document.getElementById('decision-inject-count-display');
            if (!allEl || !autoEl || !extraEl) return;

            if (countEl) countEl.textContent = defaultDecisionInjectCount;

            const confirmed = allDecisions.filter(d => d.status === 'confirmed');
            if (confirmed.length === 0) {
                allEl.innerHTML = '<p style="color: #666;">No confirmed decisions</p>';
                autoEl.innerHTML = '<p style="color: #666;">No decisions</p>';
                extraEl.innerHTML = '<p style="color: #666;">None</p>';
                return;
            }

            // Auto: latest N confirmed (sorted by timestamp DESC, so first N are latest)
            const sortedConfirmed = [...confirmed].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
            const autoIds = new Set(sortedConfirmed.slice(0, defaultDecisionInjectCount).map(d => d.id));
            const extraSet = new Set(decisionExtraSelection);

            // 左栏：所有 confirmed，点击添加到 extra
            allEl.innerHTML = sortedConfirmed.map(d => {
                const isAuto = autoIds.has(d.id);
                const isExtra = extraSet.has(d.id);
                const isSelected = isAuto || isExtra;
                const bgColor = isSelected ? '#0a1525' : '#16213e';
                const borderColor = isAuto ? '#00d9ff' : (isExtra ? '#e94560' : '#333');
                const badge = isAuto ? '<span style="background:#00d9ff;color:#000;padding:1px 4px;border-radius:3px;font-size:0.7em;margin-left:5px;">AUTO</span>' : (isExtra ? '<span style="background:#e94560;color:#fff;padding:1px 4px;border-radius:3px;font-size:0.7em;margin-left:5px;">EXTRA</span>' : '');
                const hasRange = d.message_range_start && d.message_range_end;
                const msgBtn = hasRange ? `<button onclick="event.stopPropagation();showSummaryMessages(0, ${d.message_range_start}, ${d.message_range_end})" style="padding: 1px 4px; font-size: 0.7em; background: #1f4068;" title="View source messages">📜</button>` : '';
                return `
                <div class="card" style="margin-bottom: 8px; padding: 8px; background: ${bgColor}; border-left: 3px solid ${borderColor}; cursor: pointer;" onclick="addDecisionExtra(${d.id})">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-size: 0.8em; color: #888;">#${d.id} ${formatDateTime(d.timestamp)}${badge}</span>
                        ${msgBtn}
                    </div>
                    <div style="margin-top: 5px; font-size: 0.85em;"><strong style="color: #00d9ff;">${escapeHtml(d.problem.substring(0, 60))}${d.problem.length > 60 ? '...' : ''}</strong></div>
                    <div style="margin-top: 3px; font-size: 0.8em; color: #4ad9a0;">→ ${escapeHtml(d.solution.substring(0, 60))}${d.solution.length > 60 ? '...' : ''}</div>
                </div>`;
            }).join('');

            // 右栏 Extra: 手动选中的
            const extraDecisions = decisionExtraSelection.map(id => confirmed.find(d => d.id === id)).filter(Boolean);
            extraEl.innerHTML = extraDecisions.length > 0
                ? extraDecisions.map(d => `
                    <div class="card" style="margin-bottom: 5px; padding: 6px; background: #1a1a2e; border-left: 3px solid #e94560;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.75em; color: #888;">#${d.id}</span>
                            <button onclick="event.stopPropagation();removeDecisionExtra(${d.id})" style="padding: 1px 6px; font-size: 0.7em; background: #e94560;">✕</button>
                        </div>
                        <div style="font-size: 0.8em; color: #ccc; margin-top: 3px;">${escapeHtml(d.problem.substring(0, 40))}...</div>
                    </div>
                `).join('')
                : '<p style="color: #666; font-size: 0.85em;">None selected</p>';

            // 右栏 Auto: 最新 N 个
            const autoDecisions = sortedConfirmed.slice(0, defaultDecisionInjectCount);
            autoEl.innerHTML = autoDecisions.length > 0
                ? autoDecisions.map(d => `
                    <div class="card" style="margin-bottom: 5px; padding: 6px; background: #1a1a2e; border-left: 3px solid #00d9ff;">
                        <div style="font-size: 0.75em; color: #888;">#${d.id} ${formatDateTime(d.timestamp)}</div>
                        <div style="font-size: 0.8em; color: #ccc; margin-top: 3px;">${escapeHtml(d.problem.substring(0, 40))}...</div>
                    </div>
                `).join('')
                : '<p style="color: #666; font-size: 0.85em;">No decisions</p>';
        }

        function addDecisionExtra(id) {
            const confirmed = allDecisions.filter(d => d.status === 'confirmed');
            const sortedConfirmed = [...confirmed].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
            const autoIds = new Set(sortedConfirmed.slice(0, defaultDecisionInjectCount).map(d => d.id));
            // 不能添加 auto 的，也不能重复添加
            if (!autoIds.has(id) && !decisionExtraSelection.includes(id)) {
                decisionExtraSelection.push(id);
                decisionSelectionDirty = true;
                updateDecisionDirtyHint();
                renderDecisions();
            }
        }

        function removeDecisionExtra(id) {
            decisionExtraSelection = decisionExtraSelection.filter(x => x !== id);
            decisionSelectionDirty = true;
            updateDecisionDirtyHint();
            renderDecisions();
        }

        function updateDecisionDirtyHint() {
            const hint = document.getElementById('decision-selection-dirty-hint');
            if (hint) hint.style.display = decisionSelectionDirty ? 'inline' : 'none';
        }

        async function saveDecisionSelection() {
            if (!currentProject) return;
            await fetch(`/api/projects/${currentProject}/decisions/selection`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({selected_ids: decisionExtraSelection})
            });
            decisionSelectionDirty = false;
            updateDecisionDirtyHint();
        }

        async function extractDecisions() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Extracting...';
            document.getElementById('decisions-status').textContent = 'Sending to LLM...';

            try {
                const res = await fetch(`/api/projects/${currentProject}/decisions/extract`, {method: 'POST'});
                const data = await res.json();
                btn.disabled = false;
                btn.textContent = 'Extract Now';
                document.getElementById('decisions-status').textContent = '';

                if (data.error) {
                    document.getElementById('decisions-status').textContent = 'Error: ' + data.error;
                } else {
                    document.getElementById('decisions-status').textContent = data.message;
                    loadDecisions();
                }
            } catch (e) {
                btn.disabled = false;
                btn.textContent = 'Extract Now';
                document.getElementById('decisions-status').textContent = 'Error: ' + e.message;
            }
        }

        async function loadDecisionDebug() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            document.getElementById('decisions-status').textContent = 'Loading prompt...';

            try {
                const res = await fetch(`/api/projects/${currentProject}/decisions/debug`);
                const data = await res.json();
                document.getElementById('decisions-status').textContent = '';

                const panel = document.getElementById('decision-debug-panel');
                const content = document.getElementById('decision-debug-content');
                const statusColor = data.pending_count > 0 ? '#ffcc00' : '#4caf50';
                content.textContent = `=== Latest Msg: #${data.latest_message_id || 0} | Last Extracted: → #${data.last_decision_end_id || 0} | Pending: ${data.pending_count || 0} messages ===\n=== Messages: ${data.message_count}, Conversation: ${data.conversation_length} chars ===\n\n${data.prompt}`;
                panel.style.display = 'block';
            } catch (e) {
                document.getElementById('decisions-status').textContent = 'Error: ' + e.message;
            }
        }

        function renderPendingDecision(d) {
            let options = [];
            let files = [];
            try { options = typeof d.reason_options === 'string' ? JSON.parse(d.reason_options || '[]') : (d.reason_options || []); } catch (e) {}
            try { files = typeof d.files === 'string' ? JSON.parse(d.files || '[]') : (d.files || []); } catch (e) {}
            return `
                <div class="card" style="margin-bottom: 15px; border-left: 3px solid #e94560;">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px;">
                        <span style="color: #888; font-size: 0.85em;">#${d.id} · ${new Date(d.timestamp).toLocaleString()}</span>
                        <div style="display: flex; gap: 5px;">
                            <button onclick="skipDecision(${d.id})" style="padding: 4px 10px; background: #333; font-size: 0.85em;">Skip</button>
                            <button onclick="deleteDecision(${d.id})" style="padding: 4px 10px; background: #e94560; font-size: 0.85em;">Delete</button>
                        </div>
                    </div>
                    <div style="margin-bottom: 8px;">
                        <strong style="color: #00d9ff;">Problem:</strong>
                        <div style="margin-top: 4px; color: #ccc;">${escapeHtml(d.problem)}</div>
                    </div>
                    <div style="margin-bottom: 8px;">
                        <strong style="color: #4ad9a0;">Solution:</strong>
                        <div style="margin-top: 4px; color: #ccc;">${escapeHtml(d.solution)}</div>
                    </div>
                    ${files.length > 0 ? `<div style="margin-bottom: 8px; color: #888; font-size: 0.85em;">Files: ${files.map(f => `<code>${escapeHtml(f)}</code>`).join(', ')}</div>` : ''}
                    <div style="margin-top: 10px; padding-top: 10px; border-top: 1px solid #333;">
                        <strong style="color: #d9a04a;">Select Reason:</strong>
                        <div id="reason-options-${d.id}" style="margin-top: 8px;">
                            ${options.map((opt, i) => `
                                <label style="display: block; margin-bottom: 6px; cursor: pointer;">
                                    <input type="radio" name="reason-${d.id}" value="${escapeHtml(opt)}" style="margin-right: 8px;">
                                    ${escapeHtml(opt)}
                                </label>
                            `).join('')}
                            <label style="display: block; margin-bottom: 6px; cursor: pointer;">
                                <input type="radio" name="reason-${d.id}" value="__other__" style="margin-right: 8px;">
                                Other: <input type="text" id="reason-other-${d.id}" placeholder="Enter custom reason..." style="padding: 4px 8px; width: 300px;">
                            </label>
                        </div>
                        <div style="margin-top: 10px;">
                            <label style="color: #888; font-size: 0.9em;">Note (optional):</label>
                            <textarea id="note-${d.id}" placeholder="Add any additional notes..." style="width: 100%; margin-top: 4px; padding: 8px; min-height: 60px;"></textarea>
                        </div>
                        <button onclick="confirmDecision(${d.id})" style="margin-top: 10px; padding: 8px 20px; background: #4ad9a0;">Confirm Decision</button>
                    </div>
                </div>
            `;
        }

        async function confirmDecision(id) {
            const selectedRadio = document.querySelector(`input[name="reason-${id}"]:checked`);
            if (!selectedRadio) {
                alert('Please select a reason');
                return;
            }

            let reason = selectedRadio.value;
            if (reason === '__other__') {
                reason = document.getElementById(`reason-other-${id}`).value.trim();
                if (!reason) {
                    alert('Please enter a custom reason');
                    return;
                }
            }

            const note = document.getElementById(`note-${id}`).value.trim();

            try {
                const res = await fetch(`/api/projects/${currentProject}/decisions/${id}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({status: 'confirmed', reason, note})
                });
                if (res.ok) {
                    loadDecisions();
                } else {
                    alert('Failed to confirm decision');
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        async function skipDecision(id) {
            try {
                const res = await fetch(`/api/projects/${currentProject}/decisions/${id}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({status: 'skipped'})
                });
                if (res.ok) {
                    loadDecisions();
                }
            } catch (e) {
                console.error('Error skipping decision:', e);
            }
        }

        async function deleteDecision(id) {
            if (!confirm('Delete this decision?')) return;
            try {
                const res = await fetch(`/api/projects/${currentProject}/decisions/${id}`, {method: 'DELETE'});
                if (res.ok) {
                    loadDecisions();
                }
            } catch (e) {
                console.error('Error deleting decision:', e);
            }
        }

        // 加载应用配置
        async function loadAppConfig() {
            try {
                const res = await fetch('/api/config');
                const data = await res.json();
                const cfg = data.config || {};
                const defaults = data.defaults || {};
                appConfig.summary_max_chars_total = parseInt(cfg.summary_max_chars_total || defaults.summary_max_chars_total || 8000);
                appConfig.search_result_preview_length = parseInt(cfg.search_result_preview_length || defaults.search_result_preview_length || 500);
                appConfig.dashboard_refresh_interval = parseInt(cfg.dashboard_refresh_interval || defaults.dashboard_refresh_interval || 5000);
                console.log('App config loaded:', appConfig);
            } catch (e) {
                console.error('Failed to load config:', e);
            }
        }

        // 事件通知系统
        let lastEventTime = 0;
        let displayedEvents = new Set();

        async function pollEvents() {
            try {
                const res = await fetch('/api/events');
                const data = await res.json();
                const events = data.events || [];

                const panel = document.getElementById('event-panel');

                for (const evt of events) {
                    const evtId = `${evt.type}-${evt.timestamp}`;
                    if (displayedEvents.has(evtId)) continue;
                    displayedEvents.add(evtId);

                    // 根据事件类型选择颜色和图标
                    let icon = '📌', bgColor = '#16213e', borderColor = '#4a90d9';
                    if (evt.type === 'summary' || evt.type === 'summary_done') {
                        icon = '📝'; borderColor = '#e94560';
                    } else if (evt.type === 'knowledge' || evt.type === 'knowledge_done') {
                        icon = '🧠'; borderColor = '#00d9ff';
                    } else if (evt.type === 'embedding') {
                        icon = '🔢'; borderColor = '#d9a04a';
                    } else if (evt.type === 'session' || evt.type === 'session_end') {
                        icon = '🚀'; borderColor = '#4ad9a0';
                    } else if (evt.type === 'message') {
                        icon = '💬'; borderColor = '#9a4ad9';
                    } else if (evt.type === 'error') {
                        icon = '❌'; borderColor = '#ff4444'; bgColor = '#2a1a1a';
                    }

                    const toast = document.createElement('div');
                    toast.style.cssText = `
                        background: ${bgColor}; border-left: 3px solid ${borderColor};
                        padding: 10px 12px; border-radius: 6px; margin-bottom: 8px;
                        box-shadow: 0 4px 12px rgba(0,0,0,0.4); animation: slideIn 0.3s ease;
                        font-size: 0.85em;
                    `;
                    toast.innerHTML = `
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span>${icon} <strong style="color: ${borderColor};">${escapeHtml(evt.message)}</strong></span>
                            <span style="color: #666; font-size: 0.8em;">${evt.time_str || ''}</span>
                        </div>
                        ${evt.details ? `<div style="color: #888; font-size: 0.8em; margin-top: 3px;">${escapeHtml(evt.details)}</div>` : ''}
                    `;
                    panel.appendChild(toast);

                    // 自动移除
                    setTimeout(() => {
                        toast.style.opacity = '0';
                        toast.style.transform = 'translateX(-20px)';
                        toast.style.transition = 'all 0.3s ease';
                        setTimeout(() => toast.remove(), 300);
                    }, evt.type.includes('done') ? 3000 : 5000);
                }

                // 清理旧的已显示事件 ID
                if (displayedEvents.size > 100) {
                    displayedEvents = new Set([...displayedEvents].slice(-50));
                }
            } catch (e) {
                // 忽略轮询错误
            }
        }

        // 初始化
        let refreshIntervalId = null;
        let eventPollId = null;
        async function initApp() {
            await loadAppConfig();
            await refreshAll();
            // 设置刷新间隔（0 表示禁用）
            if (appConfig.dashboard_refresh_interval > 0) {
                refreshIntervalId = setInterval(refreshAll, appConfig.dashboard_refresh_interval);
            }
            // 事件轮询（每秒）
            eventPollId = setInterval(pollEvents, 1000);
            pollEvents();
        }
        initApp();
    </script>
</body>
</html>"""


def main():
    import sys
    sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None
    print("\n" + "="*50)
    print("Hybrid Memory Dashboard")
    print("="*50)
    print("Open in browser: http://localhost:37888")
    print("="*50 + "\n")
    app.run(host="0.0.0.0", port=37888, debug=False, threaded=True)


if __name__ == "__main__":
    main()
