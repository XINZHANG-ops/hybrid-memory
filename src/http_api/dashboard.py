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
from src.memory_core import MemoryManager, ConfigManager, DEFAULT_CONFIG, CONFIG_META
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


@app.route("/api/projects/<project_name>/summaries")
def get_summaries(project_name):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT id, session_id, summary_text, message_count, created_at FROM summaries ORDER BY id DESC"
        ).fetchall()
    return json_response({
        "summaries": [{"id": r[0], "session_id": r[1], "summary_text": r[2], "message_count": r[3], "created_at": str(r[4])} for r in rows]
    })


@app.route("/api/projects/<project_name>/context")
def get_context(project_name):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)

    # 获取所有会话的摘要和最近消息（跨会话）
    all_summaries = db.get_all_summaries(limit=5)
    recent_messages = db.get_recent_messages_all_sessions(limit=10)

    context = {
        "summaries": "\n\n---\n\n".join(s.summary_text for s in all_summaries) if all_summaries else "",
        "messages": [{"role": m.role, "content": m.content} for m in reversed(recent_messages)] if recent_messages else []
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
        results = manager.vector_search(query, k=k)
        return json_response({
            "results": [{"id": m.id, "role": m.role, "content": m.content[:500], "score": round(score, 4), "timestamp": str(m.timestamp)} for m, score in results]
        })
    except Exception as e:
        return json_response({"error": str(e)}), 500


@app.route("/api/projects/<project_name>/knowledge")
def get_knowledge(project_name):
    """获取项目的结构化知识"""
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    knowledge = db.get_knowledge()
    return json_response({"knowledge": knowledge})


@app.route("/api/projects/<project_name>/summary-debug")
def get_summary_debug(project_name):
    """获取即将发送给 summary 模型的内容（用于调试）"""
    from src.memory_core.summarizer import SUMMARY_PROMPT, SUMMARY_PROMPT_WITH_CONTEXT
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        return json_response({"error": "Project not found"}), 404
    db = Database(db_path)
    with db._connect() as conn:
        row = conn.execute("SELECT session_id FROM sessions ORDER BY last_active_at DESC LIMIT 1").fetchone()
    session_id = row[0] if row else f"{project_name}-session"
    messages = db.get_unsummarized_messages(session_id)
    summaries = db.get_summaries(session_id)
    previous_context = "\n\n---\n\n".join(s.summary_text for s in summaries) if summaries else ""
    # 格式化对话
    lines = []
    for msg in messages:
        role_label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(msg.role, msg.role)
        lines.append(f"{role_label}: {msg.content}")
    conversation = "\n".join(lines)
    # 生成完整 prompt
    if previous_context:
        prompt = SUMMARY_PROMPT_WITH_CONTEXT.format(previous_context=previous_context, conversation=conversation)
    else:
        prompt = SUMMARY_PROMPT.format(conversation=conversation)
    return json_response({
        "session_id": session_id,
        "message_count": len(messages),
        "summary_count": len(summaries),
        "previous_context": previous_context[:1000] + "..." if len(previous_context) > 1000 else previous_context,
        "messages": [{"id": m.id, "role": m.role, "content": m.content[:500], "is_summarized": m.is_summarized} for m in messages],
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
        .card { background: #16213e; border-radius: 8px; padding: 15px; margin-bottom: 15px; }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .badge { background: #00d9ff; color: #1a1a2e; padding: 2px 8px; border-radius: 10px; font-size: 0.8em; }
        .message { padding: 10px; margin: 5px 0; border-radius: 5px; }
        .message.user { background: #1f4068; border-left: 3px solid #00d9ff; }
        .message.assistant { background: #0f3460; border-left: 3px solid #e94560; }
        .message-role { font-weight: bold; color: #00d9ff; margin-bottom: 5px; }
        .message-content { white-space: pre-wrap; word-break: break-word; font-size: 0.9em; line-height: 1.5; max-height: 300px; overflow-y: auto; }
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
        .refresh-btn { position: fixed; bottom: 20px; right: 20px; width: 50px; height: 50px; border-radius: 50%; font-size: 1.5em; }
        .context-preview { background: #0f3460; padding: 15px; border-radius: 8px; margin: 10px 0; }
        .context-section { margin: 10px 0; }
        .context-label { color: #00d9ff; font-weight: bold; margin-bottom: 5px; }
        .session-select { margin-bottom: 15px; }
        .no-project { color: #888; padding: 20px; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Hybrid Memory Dashboard</h1>
            <div class="global-project">
                <label>Current Project:</label>
                <select id="global-project" onchange="onProjectChange()">
                    <option value="">-- Select Project --</option>
                </select>
            </div>
        </div>

        <div class="tabs">
            <button class="tab active" onclick="showPanel('overview')">Overview</button>
            <button class="tab" onclick="showPanel('messages')">Messages</button>
            <button class="tab" onclick="showPanel('summaries')">Summaries</button>
            <button class="tab" onclick="showPanel('context')">Injected Context</button>
            <button class="tab" onclick="showPanel('search')">Global Search</button>
            <button class="tab" onclick="showPanel('vector')">Vector Search</button>
            <button class="tab" onclick="showPanel('knowledge')">Knowledge</button>
            <button class="tab" onclick="showPanel('logs')">Logs</button>
            <button class="tab" onclick="showPanel('config')">Config</button>
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
            <h2>Messages</h2>
            <div class="session-select">
                <select id="msg-session" onchange="loadMessages()">
                    <option value="">All Sessions</option>
                </select>
            </div>
            <div id="message-list"></div>
        </div>

        <!-- Summaries Panel -->
        <div id="summaries" class="panel">
            <h2>Summaries</h2>
            <div id="summary-list"></div>
        </div>

        <!-- Context Panel -->
        <div id="context" class="panel">
            <h2>Injected Context Preview</h2>
            <p style="color: #888; margin-bottom: 15px;">This shows what would be injected into Claude when starting a session.</p>
            <div id="context-preview"></div>
        </div>

        <!-- Search Panel -->
        <div id="search" class="panel">
            <h2>Search Global Memory</h2>
            <div class="search-box">
                <input type="text" id="search-query" placeholder="Search messages..." onkeypress="if(event.key==='Enter')doSearch()" style="flex: 1;">
                <label style="display: flex; align-items: center; margin: 0 10px; color: #888;">
                    <input type="checkbox" id="fuzzy-search" style="margin-right: 5px;"> Fuzzy
                </label>
                <label style="display: flex; align-items: center; margin-right: 10px; color: #888;">
                    <span style="margin-right: 5px;">Threshold:</span>
                    <input type="number" id="fuzzy-threshold" value="60" min="0" max="100" style="width: 60px; padding: 5px;">
                </label>
                <button onclick="doSearch()">Search</button>
            </div>
            <div id="search-results"></div>
        </div>

        <!-- Logs Panel -->
        <div id="logs" class="panel">
            <h2>Hook Logs</h2>
            <button onclick="loadLogs()">Refresh</button>
            <div id="log-list" style="margin-top: 15px; max-height: 600px; overflow-y: auto;"></div>
        </div>

        <!-- Config Panel -->
        <div id="config" class="panel">
            <h2>Configuration</h2>
            <div class="card">
                <div id="config-form"></div>
                <div style="margin-top: 20px; padding-top: 15px; border-top: 1px solid #333;">
                    <button onclick="saveAllConfig()" style="padding: 12px 30px; font-size: 1.1em;">Save All Settings</button>
                    <button onclick="resetConfig()" style="background: #e94560; margin-left: 10px;">Reset to Defaults</button>
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
                <div id="debug-messages" style="max-height: 300px; overflow-y: auto;"></div>
            </div>
            <div class="card" style="margin-top: 15px;">
                <h3>Full Prompt Sent to LLM</h3>
                <pre id="debug-prompt" style="background: #1a1a2e; padding: 15px; border-radius: 8px; white-space: pre-wrap; word-wrap: break-word; max-height: 400px; overflow-y: auto; font-size: 0.9em;"></pre>
            </div>
        </div>

        <!-- Vector Search Panel -->
        <div id="vector" class="panel">
            <h2>Vector Semantic Search</h2>
            <div class="card">
                <input type="text" id="vector-query" placeholder="Enter semantic query..." style="width: 70%; padding: 10px; border-radius: 5px; border: 1px solid #444; background: #1a1a2e; color: #fff;">
                <button onclick="searchVector()" style="padding: 10px 20px; margin-left: 10px;">Search</button>
            </div>
            <div id="vector-results" style="margin-top: 15px;"></div>
        </div>

        <!-- Knowledge Panel -->
        <div id="knowledge" class="panel">
            <h2>Structured Knowledge</h2>
            <button onclick="loadKnowledge()">Refresh Knowledge</button>
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

    <button class="refresh-btn" onclick="refreshAll()">&#8635;</button>

    <script>
        let projects = [];
        let currentProject = '';
        let currentPanel = 'overview';

        function showPanel(id) {
            currentPanel = id;
            document.querySelectorAll('.panel').forEach(p => p.style.display = 'none');
            document.getElementById(id).style.display = 'block';
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
        }

        function onProjectChange() {
            currentProject = document.getElementById('global-project').value;
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
            document.getElementById('global-project').value = name;
            localStorage.setItem('currentProject', name);
            loadProjectData();
        }

        async function loadSessions() {
            if (!currentProject) return;
            const res = await fetch(`/api/projects/${currentProject}/sessions`);
            const data = await res.json();
            const opts = data.sessions.map(s => `<option value="${s.session_id}">${s.session_id.substring(0,30)}...</option>`).join('');
            document.getElementById('msg-session').innerHTML = '<option value="">All Sessions</option>' + opts;
        }

        async function loadMessages() {
            if (!currentProject) return;
            const session = document.getElementById('msg-session').value;
            const url = session ? `/api/projects/${currentProject}/messages?session_id=${encodeURIComponent(session)}` : `/api/projects/${currentProject}/messages`;
            const res = await fetch(url);
            const data = await res.json();
            document.getElementById('message-list').innerHTML = data.messages.map(m => `
                <div class="message ${m.role}">
                    <div class="message-role">${m.role}</div>
                    <div class="message-content">${escapeHtml(m.content)}</div>
                    <div class="message-meta">${m.timestamp} | ${m.is_summarized ? 'Summarized' : 'Not summarized'}</div>
                </div>
            `).join('') || '<p>No messages found</p>';
        }

        async function loadSummaries() {
            if (!currentProject) return;
            const res = await fetch(`/api/projects/${currentProject}/summaries`);
            const data = await res.json();
            document.getElementById('summary-list').innerHTML = data.summaries.map(s => `
                <div class="summary">
                    <div style="margin-bottom: 10px;"><strong>Session:</strong> ${s.session_id} | <strong>Messages:</strong> ${s.message_count} | <strong>Created:</strong> ${s.created_at}</div>
                    <div class="summary-text">${escapeHtml(s.summary_text)}</div>
                </div>
            `).join('') || '<p>No summaries found</p>';
        }

        async function loadContext() {
            if (!currentProject) return;
            const res = await fetch(`/api/projects/${currentProject}/context`);
            const data = await res.json();
            let html = '<div class="context-preview">';
            if (data.summaries) {
                html += `<div class="context-section"><div class="context-label">Historical Summaries:</div><div class="summary-text">${escapeHtml(data.summaries)}</div></div>`;
            }
            if (data.messages && data.messages.length > 0) {
                html += `<div class="context-section"><div class="context-label">Recent Messages (${data.messages.length}):</div>`;
                data.messages.forEach(m => {
                    html += `<div class="message ${m.role}"><div class="message-role">${m.role}</div><div class="message-content">${escapeHtml(m.content)}</div></div>`;
                });
                html += '</div>';
            }
            if (!data.summaries && (!data.messages || data.messages.length === 0)) {
                html += '<p style="color: #888;">No context available for this project yet.</p>';
            }
            html += '</div>';
            const el = document.getElementById('context-preview');
            el.innerHTML = html;
            el.scrollTop = el.scrollHeight;
        }

        async function doSearch() {
            const query = document.getElementById('search-query').value;
            if (!query) return;
            const fuzzy = document.getElementById('fuzzy-search').checked;
            const threshold = document.getElementById('fuzzy-threshold').value || 60;
            let url = `/api/global/search?query=${encodeURIComponent(query)}`;
            if (fuzzy) {
                url += `&fuzzy=true&threshold=${threshold}`;
            }
            const res = await fetch(url);
            const data = await res.json();
            document.getElementById('search-results').innerHTML = data.results.map(m => `
                <div class="message ${m.role}">
                    <div class="message-role">${m.role} <span class="badge">${m.session_id}</span></div>
                    <div class="message-content">${escapeHtml(m.content)}</div>
                </div>
            `).join('') || '<p>No results found</p>';
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

        async function loadConfig() {
            const res = await fetch('/api/config');
            const data = await res.json();
            configMeta = data.meta || {};
            configDefaults = data.defaults || {};
            const config = data.config || {};

            const configKeys = ['short_term_window_size', 'max_context_tokens', 'summary_trigger_threshold', 'llm_provider', 'ollama_model', 'ollama_base_url', 'ollama_timeout', 'ollama_keep_alive', 'anthropic_model', 'embedding_model', 'enable_vector_search', 'enable_knowledge_extraction'];
            let html = '';
            for (const key of configKeys) {
                const meta = configMeta[key] || {label: key, description: '', type: 'text'};
                const value = config[key] || configDefaults[key] || '';
                html += `<div class="config-item" style="margin-bottom: 20px;">`;
                html += `<label style="display: block; color: #00d9ff; font-weight: bold; margin-bottom: 5px;">${meta.label || key}</label>`;
                html += `<div style="color: #888; font-size: 0.85em; margin-bottom: 8px;">${meta.description || ''}</div>`;
                if (meta.type === 'select' && meta.options) {
                    html += `<select id="config-${key}" style="width: 100%; max-width: 400px; padding: 10px;">`;
                    for (const opt of meta.options) {
                        html += `<option value="${opt}" ${value === opt ? 'selected' : ''}>${opt}</option>`;
                    }
                    html += `</select>`;
                } else if (meta.type === 'number') {
                    html += `<input type="number" id="config-${key}" value="${value}" min="${meta.min || 0}" max="${meta.max || 99999}" style="width: 100%; max-width: 400px; padding: 10px;">`;
                } else {
                    html += `<input type="text" id="config-${key}" value="${value}" style="width: 100%; max-width: 400px; padding: 10px;">`;
                }
                html += `</div>`;
            }
            document.getElementById('config-form').innerHTML = html;
        }

        async function saveAllConfig() {
            const configKeys = ['short_term_window_size', 'max_context_tokens', 'summary_trigger_threshold', 'llm_provider', 'ollama_model', 'ollama_base_url', 'ollama_timeout', 'ollama_keep_alive', 'anthropic_model', 'embedding_model', 'enable_vector_search', 'enable_knowledge_extraction'];
            for (const key of configKeys) {
                const el = document.getElementById(`config-${key}`);
                if (el) {
                    await fetch('/api/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key, value: el.value})});
                }
            }
            alert('Settings saved!');
            loadConfig();
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
            // Config/Debug/Vector/Knowledge 页面不自动刷新
            if (currentPanel === 'config' || currentPanel === 'debug' || currentPanel === 'vector' || currentPanel === 'knowledge') {
                return;
            }
            await loadProjects();
            loadProjectData();
            loadConfig();
        }

        async function searchVector() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const query = document.getElementById('vector-query').value;
            if (!query) return;
            const res = await fetch(`/api/projects/${currentProject}/vector-search?query=${encodeURIComponent(query)}&k=10`);
            const data = await res.json();
            if (data.error) {
                document.getElementById('vector-results').innerHTML = `<div class="card" style="color: #ff6b6b;">Error: ${data.error}</div>`;
                return;
            }
            document.getElementById('vector-results').innerHTML = data.results.map(r => `
                <div class="card" style="margin-bottom: 10px;">
                    <div style="display: flex; justify-content: space-between;">
                        <span style="color: ${r.role === 'user' ? '#00d9ff' : '#ff6b9d'}; font-weight: bold;">${r.role}</span>
                        <span style="color: #888;">Score: ${r.score}</span>
                    </div>
                    <div style="margin-top: 8px; color: #ccc;">${escapeHtml(r.content)}</div>
                    <div style="margin-top: 5px; font-size: 0.8em; color: #666;">${r.timestamp}</div>
                </div>
            `).join('') || '<div class="card" style="color: #888;">No results found</div>';
        }

        async function loadKnowledge() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const res = await fetch(`/api/projects/${currentProject}/knowledge`);
            const data = await res.json();
            const k = data.knowledge || {};
            const categories = ['user-preferences', 'project-decisions', 'key-facts', 'pending-tasks', 'learned-patterns', 'important-context'];
            const keys = ['user_preferences', 'project_decisions', 'key_facts', 'pending_tasks', 'learned_patterns', 'important_context'];
            for (let i = 0; i < categories.length; i++) {
                const items = k[keys[i]] || [];
                document.getElementById(`k-${categories[i]}`).innerHTML = items.length > 0
                    ? items.map(item => `<li style="margin: 5px 0; color: #ccc;">${escapeHtml(item)}</li>`).join('')
                    : '<li style="color: #666;">No items</li>';
            }
        }

        setInterval(refreshAll, 5000);
        refreshAll();
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
