#!/usr/bin/env python3
"""
Hybrid Memory Dashboard - Web UI
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from flask import Flask, jsonify, request, Response
import json
from src.memory_core import (
    MemoryManager, ConfigManager, DEFAULT_CONFIG, CONFIG_META,
    EXTRACTION_PROMPT, CONDENSE_PROMPT, SUMMARY_PROMPT, SUMMARY_PROMPT_WITH_CONTEXT
)
from src.memory_core.database import Database

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 支持中文

MEMORY_BASE = Path(__file__).parent.parent.parent / "data"
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
    for p in projects:
        db_path = PROJECTS_DIR / f"{p}.db"
        try:
            db = Database(db_path)
            with db._connect() as conn:
                msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                summary_count = conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
            result.append({
                "name": p,
                "messages": msg_count,
                "sessions": session_count,
                "summaries": summary_count,
            })
        except Exception as e:
            result.append({"name": p, "error": str(e)})
    return json_response({"projects": result})


@app.route("/api/projects/<project_name>/sessions")
def get_sessions(project_name):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT session_id, started_at, last_active_at, is_active FROM sessions ORDER BY last_active_at DESC"
        ).fetchall()
    return json_response({
        "sessions": [{"session_id": r[0], "started_at": str(r[1]), "last_active_at": str(r[2]), "is_active": bool(r[3])} for r in rows]
    })


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
                "SELECT id, session_id, role, content, timestamp, is_summarized FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, session_id, role, content, timestamp, is_summarized FROM messages ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return json_response({
        "messages": [{"id": r[0], "session_id": r[1], "role": r[2], "content": r[3], "timestamp": str(r[4]), "is_summarized": bool(r[5])} for r in rows]
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
    return json_response({
        "messages": [{"id": m.id, "role": m.role, "content": m.content, "timestamp": str(m.timestamp)} for m in messages]
    })


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
    return json_response({"status": "ok", "summary_text": new_text})


@app.route("/api/projects/<project_name>/summaries/regenerate-all", methods=["POST"])
def regenerate_all_summaries(project_name):
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
    regenerated = 0
    for i, summary in enumerate(all_summaries):
        if not summary.message_range_start or not summary.message_range_end:
            continue
        messages = db.get_messages_in_range(summary.message_range_start, summary.message_range_end)
        if not messages:
            continue
        previous_context = "\n\n---\n\n".join(s.summary_text for s in all_summaries[:i][-3:]) if i > 0 else ""
        new_text = generator.generate(messages, previous_context, custom_template)
        db.update_summary_text(summary.id, new_text)
        regenerated += 1
    return json_response({"status": "ok", "regenerated": regenerated})


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
    inject_preview_length = config_mgr.get_int("inject_preview_length")
    inject_knowledge_count = config_mgr.get_int("inject_knowledge_count")
    inject_task_count = config_mgr.get_int("inject_task_count")

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

    # 截取消息内容（与注入时一致）
    def truncate(text, max_len):
        if max_len <= 0 or len(text) <= max_len:
            return text
        return text[:max_len] + "..."

    context = {
        "summaries": "\n\n---\n\n".join(s.summary_text for s in summaries) if summaries else "",
        "messages": [{"role": m.role, "content": truncate(m.content, inject_preview_length)} for m in reversed(recent_messages)] if recent_messages else [],
        "knowledge": knowledge
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
                        "timestamp": str(msg.timestamp), "method": "vector"
                    })
            elif method == "bm25":
                for msg, score in manager.bm25_search(query, limit=limit):
                    proj_results.append({
                        "id": msg.id, "project": proj_name, "role": msg.role,
                        "content": msg.content, "score": round(score, 4),
                        "timestamp": str(msg.timestamp), "method": "bm25"
                    })
            elif method == "fuzzy":
                for msg in manager.search_memory(query, fuzzy=True, threshold=threshold):
                    proj_results.append({
                        "id": msg.id, "project": proj_name, "role": msg.role,
                        "content": msg.content, "score": 0,
                        "timestamp": str(msg.timestamp), "method": "fuzzy"
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
                        "vector_score": round(v_s, 3), "bm25_score": round(b_s, 3)
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
    auto_condense = config_mgr.get("knowledge_auto_condense") == "true"

    # 检查是否有类别超过限制
    needs_condense = any(len(items) > max_per_category for items in knowledge.values())

    return json_response({
        "knowledge": knowledge,
        "max_per_category": max_per_category,
        "needs_condense": needs_condense,
        "auto_condense": auto_condense,
    })


@app.route("/api/projects/<project_name>/knowledge/condense", methods=["POST"])
def condense_knowledge(project_name):
    """精炼知识：将超过限制的类别压缩到指定数量"""
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    knowledge = db.get_knowledge()

    from src.memory_core.config import load_config
    from src.memory_core.knowledge_extractor import KnowledgeExtractor
    from src.memory_core.llm_client import create_llm_client

    config_mgr = load_config(GLOBAL_DB)
    max_per_category = config_mgr.get_int("knowledge_max_items_per_category")

    # 检查是否需要精炼
    needs_condense = any(len(items) > max_per_category for items in knowledge.values())
    if not needs_condense:
        return json_response({"status": "ok", "message": "No condensing needed", "knowledge": knowledge})

    from src.memory_core.events import publish_event
    total_before = sum(len(v) for v in knowledge.values())
    publish_event("knowledge", f"Condensing {total_before} knowledge items", project_name)

    # 创建 LLM 客户端
    llm_client = create_llm_client(
        provider=config_mgr.get("llm_provider"),
        ollama_model=config_mgr.get("ollama_model"),
        ollama_base_url=config_mgr.get("ollama_base_url"),
        ollama_timeout=float(config_mgr.get("ollama_timeout")),
        ollama_keep_alive=config_mgr.get("ollama_keep_alive"),
    )

    extractor = KnowledgeExtractor(llm_client)
    condensed = extractor.condense_knowledge(knowledge, max_per_category)

    # 更新数据库：删除旧的知识，保存精炼后的
    with db._connect() as conn:
        conn.execute("DELETE FROM knowledge")
    db.save_knowledge(None, condensed)

    total_after = sum(len(v) for v in condensed.values())
    publish_event("knowledge_done", f"Condensed: {total_before} → {total_after} items", project_name)

    return json_response({"status": "ok", "knowledge": condensed})


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

    # 获取最近的未总结消息
    messages = db.get_unsummarized_messages()
    if not messages:
        # 如果没有未总结的，获取最近 20 条消息
        messages = db.get_recent_messages_all_sessions(limit=20)

    if not messages:
        return json_response({"status": "ok", "message": "No messages to extract from", "extracted": {}})

    publish_event("knowledge", f"Extracting knowledge from {len(messages)} messages", project_name)

    # 创建 LLM 客户端
    llm_client = create_llm_client(
        provider=config_mgr.get("llm_provider"),
        ollama_model=config_mgr.get("ollama_model"),
        ollama_base_url=config_mgr.get("ollama_base_url"),
        ollama_timeout=float(config_mgr.get("ollama_timeout")),
        ollama_keep_alive=config_mgr.get("ollama_keep_alive"),
    )

    max_chars = config_mgr.get_int("knowledge_max_chars_per_message")
    extractor = KnowledgeExtractor(llm_client, max_chars_per_message=max_chars)

    # 获取已有知识，传给提取器避免重复
    existing = db.get_knowledge()

    # 提取新知识（模型能看到已有知识）
    new_knowledge = extractor.extract(messages, existing)

    # 合并新旧知识
    merged = extractor.merge_knowledge(existing, new_knowledge)

    # 保存
    db.save_knowledge(None, merged)

    total_items = sum(len(v) for v in new_knowledge.values())
    publish_event("knowledge_done", f"Extracted {total_items} new items", project_name)

    return json_response({"status": "ok", "extracted": new_knowledge, "total": merged})


@app.route("/api/projects/<project_name>/knowledge-debug")
def get_knowledge_debug(project_name):
    """获取知识提取的 prompt 和输入数据（用于调试）"""
    from src.memory_core.knowledge_extractor import EXTRACTION_PROMPT
    from src.memory_core.config import load_config
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    config_mgr = load_config(GLOBAL_DB)
    max_chars = config_mgr.get_int("knowledge_max_chars_per_message")

    # 获取消息
    messages = db.get_unsummarized_messages()
    source = "unsummarized"
    if not messages:
        messages = db.get_recent_messages_all_sessions(limit=20)
        source = "recent_20"

    # 格式化对话（与 KnowledgeExtractor 一致）
    lines = []
    for msg in messages:
        role_label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(msg.role, msg.role)
        content = msg.content[:max_chars] if len(msg.content) > max_chars else msg.content
        lines.append(f"{role_label}: {content}")
    conversation = "\n".join(lines)

    # 获取已有知识并格式化
    existing_knowledge = db.get_knowledge()
    category_names = {
        "user_preferences": "用户偏好",
        "project_decisions": "项目决策",
        "key_facts": "关键事实",
        "pending_tasks": "待办事项",
        "learned_patterns": "行为模式",
        "important_context": "重要上下文",
    }
    if existing_knowledge:
        knowledge_lines = []
        for key, items in existing_knowledge.items():
            if items:
                name = category_names.get(key, key)
                knowledge_lines.append(f"- {name}: {', '.join(items[:10])}")
        existing_str = "\n".join(knowledge_lines) if knowledge_lines else "(无已有知识)"
    else:
        existing_str = "(无已有知识)"

    # 生成完整 prompt
    prompt = EXTRACTION_PROMPT.format(conversation=conversation, existing_knowledge=existing_str)

    return json_response({
        "message_count": len(messages),
        "message_source": source,
        "max_chars_per_message": max_chars,
        "messages": [{"id": m.id, "role": m.role, "content": m.content[:max_chars]} for m in messages],
        "formatted_conversation": conversation,
        "existing_knowledge": existing_str,
        "full_prompt": prompt,
    })


@app.route("/api/projects/<project_name>/summary-debug")
def get_summary_debug(project_name):
    """获取即将发送给 summary 模型的内容（用于调试）- 与实际 summarizer 逻辑完全一致"""
    from src.memory_core.summarizer import SUMMARY_PROMPT, SUMMARY_PROMPT_WITH_CONTEXT
    from src.memory_core.config import load_config
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    config_mgr = load_config(GLOBAL_DB)
    custom_template = config_mgr.get("summary_prompt_template")
    # 读取配置的截断参数
    max_chars_total = config_mgr.get_int("summary_max_chars_total")
    max_chars_per_message = config_mgr.get_int("summary_max_chars_per_message")

    with db._connect() as conn:
        row = conn.execute("SELECT session_id FROM sessions ORDER BY last_active_at DESC LIMIT 1").fetchone()
    session_id = row[0] if row else f"{project_name}-session"
    messages = db.get_unsummarized_messages(session_id)
    summaries = db.get_summaries(session_id)
    previous_context = "\n\n---\n\n".join(s.summary_text for s in summaries) if summaries else ""

    # 格式化对话 - 与 summarizer._format_conversation 完全一致（使用配置参数）
    lines = []
    total_chars = 0
    for msg in reversed(messages):
        role_label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(msg.role, msg.role)
        content = msg.content[:max_chars_per_message] if len(msg.content) > max_chars_per_message else msg.content
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

    return json_response({
        "session_id": session_id,
        "message_count": len(messages),
        "summary_count": len(summaries),
        "using_custom_template": using_custom,
        "previous_context": previous_context[:1000] + "..." if len(previous_context) > 1000 else previous_context,
        "messages": [{"id": m.id, "role": m.role, "content": m.content[:max_chars_per_message] if len(m.content) > max_chars_per_message else m.content, "is_summarized": m.is_summarized} for m in messages],
        "formatted_conversation": conversation,
        "full_prompt": prompt,
    })


@app.route("/api/logs")
def get_logs():
    log_file = MEMORY_BASE / "hooks.log"
    if not log_file.exists():
        return json_response({"logs": []})
    lines = int(request.args.get("lines", 100))
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    return json_response({"logs": all_lines[-lines:]})


@app.route("/api/config")
def get_config():
    db = Database(GLOBAL_DB)
    config_mgr = ConfigManager(db)
    return json_response({
        "config": config_mgr.get_all(),
        "defaults": DEFAULT_CONFIG,
        "meta": CONFIG_META,
        "default_prompts": {
            "summary_prompt_template": SUMMARY_PROMPT_WITH_CONTEXT,
            "knowledge_extraction_prompt": EXTRACTION_PROMPT,
            "knowledge_condense_prompt": CONDENSE_PROMPT,
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
        .log-line { font-family: monospace; font-size: 0.75em; padding: 2px 0; border-bottom: 1px solid #333; white-space: pre-wrap; word-break: break-all; }
        .log-line.error { color: #e94560; }
        .log-line.info { color: #00d9ff; }
        .log-line.debug { color: #666; }
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
            <button class="tab" onclick="showPanel('summaries')">Summaries</button>
            <button class="tab" onclick="showPanel('context')">Injected Context</button>
            <button class="tab" onclick="showPanel('search')">Search</button>
            <button class="tab" onclick="showPanel('knowledge')">Knowledge</button>
            <button class="tab" onclick="showPanel('logs')">Logs</button>
            <button class="tab" onclick="showPanel('debug')">Debug</button>
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
                <button onclick="regenerateAllSummaries()" style="background: #e94560;">Regenerate All</button>
                <button onclick="saveSummarySelection()">Save Extra Selection</button>
                <span id="selection-dirty-hint" style="color: #ffcc00; margin-left: 5px; display: none;">● Unsaved</span>
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
            <div style="margin-bottom: 10px; color: #666; font-size: 0.85em;">
                <span id="search-status"></span>
            </div>
            <div id="search-results"></div>
        </div>

        <!-- Logs Panel -->
        <div id="logs" class="panel">
            <h2>Hook Logs</h2>
            <button onclick="loadLogs()">Refresh</button>
            <div id="log-list" style="margin-top: 15px;"></div>
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

        <!-- Debug Panel -->
        <div id="debug" class="panel">
            <h2>Summary Debug</h2>
            <button onclick="loadSummaryDebug()">Load Summary Prompt</button>
            <div class="card" style="margin-top: 15px;">
                <h3>Session Info</h3>
                <div id="debug-info" style="color: #888;">Click "Load Summary Prompt" to view</div>
            </div>
            <div class="card" style="margin-top: 15px;">
                <h3>Unsummarized Messages (<span id="debug-msg-count">0</span>)</h3>
                <div id="debug-messages"></div>
            </div>
            <div class="card" style="margin-top: 15px;">
                <h3>Full Prompt Sent to LLM</h3>
                <pre id="debug-prompt" style="background: #1a1a2e; padding: 15px; border-radius: 8px; white-space: pre-wrap; word-wrap: break-word; font-size: 0.9em;"></pre>
            </div>
        </div>


        <!-- Knowledge Panel -->
        <div id="knowledge" class="panel">
            <h2>Structured Knowledge</h2>
            <div style="display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 15px;">
                <button onclick="loadKnowledge()">Refresh</button>
                <button onclick="extractKnowledge()" style="background: #4a90d9;">Extract Now</button>
                <button id="condense-btn" onclick="condenseKnowledge()" style="background: #e94560; display: none;">Condense</button>
                <button onclick="loadKnowledgeDebug()" style="background: #1f4068;">View Prompt</button>
            </div>
            <div id="knowledge-debug-panel" style="display: none; margin-bottom: 15px;">
                <div class="card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                        <h3 style="margin: 0;">Knowledge Extraction Prompt</h3>
                        <button onclick="document.getElementById('knowledge-debug-panel').style.display='none'" style="background: #333; padding: 4px 10px;">Close</button>
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
            <div id="knowledge-content" style="margin-top: 15px;">
                <div class="card">
                    <h3>User Preferences</h3>
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

    <script>
        let projects = [];
        let currentProject = '';
        let currentPanel = 'overview';
        let currentSession = '';
        let sessionsData = [];
        // 全局配置（从后端加载）
        let appConfig = {
            summary_max_chars_total: 8000,
            summary_max_chars_per_message: 500,
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
                document.getElementById('summary-list').innerHTML = '<div class="no-project">Please select a project</div>';
                document.getElementById('context-preview').innerHTML = '<div class="no-project">Please select a project</div>';
                return;
            }
            loadSessions();
            loadMessages();
            loadSummaries();
            loadContext();
        }

        async function loadProjects() {
            const res = await fetch('/api/projects');
            const data = await res.json();
            projects = data.projects;

            // Stats
            let totalMsgs = 0, totalSums = 0;
            projects.forEach(p => { totalMsgs += p.messages || 0; totalSums += p.summaries || 0; });
            document.getElementById('stats').innerHTML = `
                <div class="card stat"><div class="stat-value">${projects.length}</div><div class="stat-label">Projects</div></div>
                <div class="card stat"><div class="stat-value">${totalMsgs}</div><div class="stat-label">Total Messages</div></div>
                <div class="card stat"><div class="stat-value">${totalSums}</div><div class="stat-label">Summaries</div></div>
            `;

            // Project list
            document.getElementById('project-list').innerHTML = projects.map(p => `
                <div class="card" style="cursor: pointer;" onclick="selectProject('${p.name}')">
                    <div class="card-header"><strong>${p.name}</strong><span class="badge">${p.messages || 0} msgs</span></div>
                    <div>Sessions: ${p.sessions || 0} | Summaries: ${p.summaries || 0}</div>
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

        function renderAssistantMessage(m) {
            const parts = parseAssistantContent(m.content);
            let html = `<div style="display: flex; justify-content: flex-start; margin-bottom: 20px;">
                <div style="max-width: 85%; width: 100%;">
                    <div style="font-size: 0.75em; color: #e94560; margin-bottom: 8px; font-weight: bold;">Assistant</div>`;

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
            const url = currentSession ? `/api/projects/${currentProject}/messages?session_id=${encodeURIComponent(currentSession)}` : `/api/projects/${currentProject}/messages`;
            const res = await fetch(url);
            const data = await res.json();
            const messages = [...(data.messages || [])].reverse();
            const listEl = document.getElementById('message-list');
            listEl.innerHTML = messages.map(m => {
                if (m.role === 'user') {
                    return `
                    <div style="display: flex; justify-content: flex-end; margin-bottom: 20px;">
                        <div style="max-width: 80%; background: #1a3a5c; border-radius: 12px; padding: 12px 16px; border-left: 3px solid #00d9ff;">
                            <div style="font-size: 0.75em; color: #00d9ff; margin-bottom: 6px; font-weight: bold;">You</div>
                            <div style="white-space: pre-wrap; word-break: break-word; line-height: 1.5; color: #eee;">${escapeHtml(m.content)}</div>
                            <div style="font-size: 0.7em; color: #666; margin-top: 8px; text-align: right;">${m.timestamp} ${m.is_summarized ? '✓ Summarized' : ''}</div>
                        </div>
                    </div>`;
                } else {
                    return renderAssistantMessage(m);
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

            // 使用配置的截断逻辑（与 summarizer 一致）
            const maxPerMsg = appConfig.summary_max_chars_per_message;
            const maxTotal = appConfig.summary_max_chars_total;
            let totalChars = 0;
            let includedCount = 0;

            // 从后往前计算哪些消息会被包含
            const reversed = [...messages].reverse();
            for (const m of reversed) {
                const contentLen = Math.min(m.content.length, maxPerMsg);
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
                const isTruncated = m.content.length > maxPerMsg;
                const displayContent = isTruncated ? m.content.substring(0, maxPerMsg) : m.content;

                const excludedStyle = isExcluded ? 'opacity: 0.4;' : '';
                const excludedBadge = isExcluded ? '<span style="background: #666; color: #ccc; padding: 1px 4px; border-radius: 3px; font-size: 0.7em; margin-left: 5px;">未包含</span>' : '';
                const truncatedBadge = isTruncated && !isExcluded ? `<span style="background: #4a3000; color: #ffcc00; padding: 1px 4px; border-radius: 3px; font-size: 0.7em; margin-left: 5px;">截断至${maxPerMsg}字</span>` : '';

                return `<div style="display: flex; justify-content: ${isUser ? 'flex-end' : 'flex-start'}; margin-bottom: 10px; ${excludedStyle}">
                    <div style="max-width: 90%; background: ${bgColor}; border-radius: 8px; padding: 10px; border-left: 3px solid ${borderColor};">
                        <div style="font-size: 0.7em; color: ${borderColor}; font-weight: bold;">${isUser ? 'You' : 'Assistant'}${excludedBadge}${truncatedBadge}</div>
                        <div style="white-space: pre-wrap; word-break: break-word; font-size: 0.85em; color: #eee;">${escapeHtml(displayContent)}${isTruncated && !isExcluded ? '<span style="color: #888;">...</span>' : ''}</div>
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
                let knowledgeHtml = '';
                if (k.user_preferences && k.user_preferences.length > 0) {
                    knowledgeHtml += `<div style="margin: 5px 0;"><strong>用户偏好:</strong> ${escapeHtml(k.user_preferences.join(', '))}</div>`;
                }
                if (k.project_decisions && k.project_decisions.length > 0) {
                    knowledgeHtml += `<div style="margin: 5px 0;"><strong>项目决策:</strong> ${escapeHtml(k.project_decisions.join(', '))}</div>`;
                }
                if (k.key_facts && k.key_facts.length > 0) {
                    knowledgeHtml += `<div style="margin: 5px 0;"><strong>关键事实:</strong> ${escapeHtml(k.key_facts.join(', '))}</div>`;
                }
                if (k.pending_tasks && k.pending_tasks.length > 0) {
                    knowledgeHtml += `<div style="margin: 5px 0;"><strong>待办事项:</strong> ${escapeHtml(k.pending_tasks.join(', '))}</div>`;
                }
                if (k.learned_patterns && k.learned_patterns.length > 0) {
                    knowledgeHtml += `<div style="margin: 5px 0;"><strong>行为模式:</strong> ${escapeHtml(k.learned_patterns.join(', '))}</div>`;
                }
                if (k.important_context && k.important_context.length > 0) {
                    knowledgeHtml += `<div style="margin: 5px 0;"><strong>重要上下文:</strong> ${escapeHtml(k.important_context.join(', '))}</div>`;
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
            if (!data.summaries && (!data.messages || data.messages.length === 0) && !data.knowledge) {
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

        async function doUnifiedSearch() {
            const query = document.getElementById('search-query').value;
            if (!query) return;

            const scope = document.getElementById('search-scope').value;
            const method = document.getElementById('search-method').value;
            const threshold = document.getElementById('fuzzy-threshold').value || 60;

            if (scope === 'current' && !currentProject) {
                document.getElementById('search-results').innerHTML = '<div class="card" style="color: #888;">Please select a project first, or search all projects.</div>';
                return;
            }

            document.getElementById('search-status').textContent = 'Searching...';
            document.getElementById('search-results').innerHTML = '';

            let url = `/api/search?query=${encodeURIComponent(query)}&method=${method}&scope=${scope}&limit=30`;
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

                const methodLabels = {vector: '🧠 Semantic', bm25: '🔑 Keyword', fuzzy: '〰️ Fuzzy', combined: '🔗 Combined'};
                document.getElementById('search-status').textContent = `Found ${data.total || 0} results using ${methodLabels[data.method] || data.method}`;

                document.getElementById('search-results').innerHTML = data.results.map(r => {
                    const isUser = r.role === 'user';
                    const bgColor = isUser ? '#1a3a5c' : '#2d1f3d';
                    const borderColor = isUser ? '#00d9ff' : '#e94560';
                    const methodBadge = r.method ? `<span style="background: ${r.method === 'vector' ? '#4a90d9' : '#d94a90'}; color: #fff; padding: 1px 6px; border-radius: 3px; font-size: 0.7em; margin-left: 5px;">${r.method}</span>` : '';
                    const scoreBadge = r.score > 0 ? `<span style="color: #888; font-size: 0.8em; margin-left: 8px;">score: ${r.score}</span>` : '';
                    const projectBadge = scope === 'all' ? `<span class="badge" style="margin-left: 5px;">${r.project}</span>` : '';
                    return `
                    <div class="card" style="margin-bottom: 10px; background: ${bgColor}; border-left: 3px solid ${borderColor};">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                            <div>
                                <span style="color: ${borderColor}; font-weight: bold;">${r.role}</span>
                                ${projectBadge}${methodBadge}${scoreBadge}
                            </div>
                            <span style="color: #666; font-size: 0.75em;">${r.timestamp}</span>
                        </div>
                        <div style="white-space: pre-wrap; word-break: break-word; line-height: 1.5; color: #eee;">${escapeHtml(r.content)}</div>
                    </div>`;
                }).join('') || '<div class="card" style="color: #888;">No results found</div>';
            } catch (e) {
                document.getElementById('search-status').textContent = `Error: ${e.message}`;
            }
        }

        // Legacy search for backward compatibility
        async function doSearch() {
            doUnifiedSearch();
        }

        async function loadLogs() {
            const res = await fetch('/api/logs?lines=200');
            const data = await res.json();
            const el = document.getElementById('log-list');
            el.innerHTML = data.logs.map(line => {
                let cls = 'log-line';
                if (line.includes('ERROR')) cls += ' error';
                else if (line.includes('INFO')) cls += ' info';
                else if (line.includes('DEBUG')) cls += ' debug';
                return `<div class="${cls}">${escapeHtml(line)}</div>`;
            }).join('');
            el.scrollTop = el.scrollHeight;
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

            const groups = {
                'Memory': {
                    icon: '🧠',
                    keys: ['short_term_window_size', 'max_context_tokens', 'summary_trigger_threshold']
                },
                'LLM': {
                    icon: '🤖',
                    keys: ['llm_provider', 'ollama_model', 'ollama_base_url', 'ollama_timeout', 'ollama_keep_alive', 'anthropic_model']
                },
                'Search': {
                    icon: '🔍',
                    keys: ['embedding_model', 'enable_vector_search', 'search_result_preview_length']
                },
                'Knowledge': {
                    icon: '📚',
                    keys: ['enable_knowledge_extraction', 'knowledge_max_chars_per_message', 'knowledge_max_items_per_category', 'knowledge_auto_condense']
                },
                'Inject': {
                    icon: '💉',
                    keys: ['inject_summary_count', 'inject_recent_count', 'inject_preview_length', 'inject_knowledge_count', 'inject_task_count']
                },
                'Summary': {
                    icon: '📝',
                    keys: ['summary_max_chars_total', 'summary_max_chars_per_message']
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
                    keys: ['summary_prompt_template', 'knowledge_extraction_prompt', 'knowledge_condense_prompt']
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
                        itemHtml += `<option value="${opt}" ${value === opt ? 'selected' : ''}>${opt}</option>`;
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
                'anthropic_model', 'embedding_model', 'enable_vector_search', 'enable_knowledge_extraction',
                'input_token_price', 'output_token_price',
                'inject_summary_count', 'inject_recent_count', 'inject_preview_length', 'inject_knowledge_count', 'inject_task_count',
                'summary_max_chars_total', 'summary_max_chars_per_message',
                'knowledge_max_chars_per_message', 'knowledge_max_items_per_category', 'knowledge_auto_condense',
                'search_result_preview_length', 'dashboard_refresh_interval',
                'summary_prompt_template', 'knowledge_extraction_prompt', 'knowledge_condense_prompt'
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

        async function loadSummaryDebug() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const res = await fetch(`/api/projects/${currentProject}/summary-debug`);
            const data = await res.json();
            document.getElementById('debug-info').innerHTML = `
                <div><strong>Session ID:</strong> ${data.session_id}</div>
                <div><strong>Message Count:</strong> ${data.message_count}</div>
            `;
            document.getElementById('debug-msg-count').textContent = data.message_count;
            document.getElementById('debug-messages').innerHTML = data.messages.map(m => `
                <div class="message ${m.role}" style="margin: 5px 0; padding: 8px; border-radius: 4px; background: ${m.role === 'user' ? '#1a3a4a' : '#2a1a4a'};">
                    <div style="color: ${m.role === 'user' ? '#00d9ff' : '#ff6b9d'}; font-weight: bold;">${m.role} (id: ${m.id})</div>
                    <div style="font-size: 0.9em; color: #ccc;">${escapeHtml(m.content)}</div>
                </div>
            `).join('') || '<p style="color: #888;">No unsummarized messages</p>';
            document.getElementById('debug-prompt').textContent = data.full_prompt || 'No prompt generated';
        }

        async function refreshAll() {
            // Debug/Vector/Knowledge 页面不自动刷新
            if (currentPanel === 'debug' || currentPanel === 'vector' || currentPanel === 'knowledge') {
                return;
            }
            // 编辑中不刷新，避免打断用户操作
            if (isEditing || selectionDirty) {
                return;
            }
            await loadProjects();
            loadProjectData();
        }


        async function loadKnowledge() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const res = await fetch(`/api/projects/${currentProject}/knowledge`);
            let data = await res.json();

            // 自动精炼：如果启用且需要精炼
            if (data.auto_condense && data.needs_condense) {
                const condenseBtn = document.getElementById('condense-btn');
                if (condenseBtn) {
                    condenseBtn.disabled = true;
                    condenseBtn.textContent = 'Condensing...';
                }
                const condenseRes = await fetch(`/api/projects/${currentProject}/knowledge/condense`, {method: 'POST'});
                data = await condenseRes.json();
                if (condenseBtn) {
                    condenseBtn.disabled = false;
                    condenseBtn.textContent = 'Condense Knowledge';
                }
            }

            const k = data.knowledge || {};
            const maxPerCategory = data.max_per_category || 10;
            const categories = ['user-preferences', 'project-decisions', 'key-facts', 'pending-tasks', 'learned-patterns', 'important-context'];
            const keys = ['user_preferences', 'project_decisions', 'key_facts', 'pending_tasks', 'learned_patterns', 'important_context'];
            for (let i = 0; i < categories.length; i++) {
                const items = k[keys[i]] || [];
                const isOverLimit = items.length > maxPerCategory;
                const badge = isOverLimit ? `<span style="color: #ffcc00; font-size: 0.8em;"> (${items.length}/${maxPerCategory} - needs condensing)</span>` : ` (${items.length})`;
                document.getElementById(`k-${categories[i]}`).innerHTML = items.length > 0
                    ? `<div style="color: #888; margin-bottom: 5px; font-size: 0.85em;">${badge}</div>` + items.map(item => `<li style="margin: 5px 0; color: #ccc;">${escapeHtml(item)}</li>`).join('')
                    : '<li style="color: #666;">No items</li>';
            }

            // 更新手动精炼按钮状态
            const condenseBtn = document.getElementById('condense-btn');
            if (condenseBtn) {
                condenseBtn.style.display = data.needs_condense ? 'inline-block' : 'none';
            }
        }

        async function condenseKnowledge() {
            if (!currentProject) return;
            const btn = document.getElementById('condense-btn');
            btn.disabled = true;
            btn.textContent = 'Condensing...';
            await fetch(`/api/projects/${currentProject}/knowledge/condense`, {method: 'POST'});
            btn.disabled = false;
            btn.textContent = 'Condense';
            loadKnowledge();
        }

        async function extractKnowledge() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Extracting...';
            const res = await fetch(`/api/projects/${currentProject}/knowledge/extract`, {method: 'POST'});
            const data = await res.json();
            btn.disabled = false;
            btn.textContent = 'Extract Now';
            if (data.error) {
                alert('Error: ' + data.error);
                return;
            }
            loadKnowledge();
        }

        async function loadKnowledgeDebug() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const res = await fetch(`/api/projects/${currentProject}/knowledge-debug`);
            const data = await res.json();

            document.getElementById('knowledge-debug-info').innerHTML = `
                <div>Source: <strong>${data.message_source}</strong> | Messages: <strong>${data.message_count}</strong> | Max chars/msg: <strong>${data.max_chars_per_message}</strong></div>
            `;

            document.getElementById('knowledge-debug-messages').innerHTML = data.messages.map(m => `
                <div style="padding: 5px; margin: 3px 0; background: ${m.role === 'user' ? '#1a3a5c' : '#2d1f3d'}; border-radius: 4px; font-size: 0.85em;">
                    <span style="color: ${m.role === 'user' ? '#00d9ff' : '#e94560'}; font-weight: bold;">${m.role}</span>
                    <span style="color: #888; margin-left: 8px;">${escapeHtml(m.content.substring(0, 100))}...</span>
                </div>
            `).join('') || '<div style="color: #888;">No messages</div>';

            document.getElementById('knowledge-debug-existing').textContent = data.existing_knowledge || '(无已有知识)';
            document.getElementById('knowledge-debug-prompt').textContent = data.full_prompt || 'No prompt';
            document.getElementById('knowledge-debug-panel').style.display = 'block';
        }

        // 加载应用配置
        async function loadAppConfig() {
            try {
                const res = await fetch('/api/config');
                const data = await res.json();
                const cfg = data.config || {};
                const defaults = data.defaults || {};
                appConfig.summary_max_chars_total = parseInt(cfg.summary_max_chars_total || defaults.summary_max_chars_total || 8000);
                appConfig.summary_max_chars_per_message = parseInt(cfg.summary_max_chars_per_message || defaults.summary_max_chars_per_message || 500);
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
