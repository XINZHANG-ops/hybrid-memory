import os
import sys
from pathlib import Path
from typing import Literal
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from src.memory_core import MemoryManager
from src.memory_core.database import Database

app = FastAPI(title="Hybrid Memory API")

MEMORY_BASE = Path(__file__).parent.parent.parent / "data"
GLOBAL_DB = MEMORY_BASE / "global_memory.db"
PROJECTS_DIR = MEMORY_BASE / "projects"

_managers: dict[str, MemoryManager] = {}


def get_manager(db_path: Path) -> MemoryManager:
    key = str(db_path)
    if key not in _managers:
        _managers[key] = MemoryManager(db_path=db_path)
    return _managers[key]


def get_project_list() -> list[str]:
    if not PROJECTS_DIR.exists():
        return []
    return [f.stem for f in PROJECTS_DIR.glob("*.db")]


class AddMessageRequest(BaseModel):
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: str


class ContextRequest(BaseModel):
    session_id: str
    max_tokens: int | None = None


class SearchRequest(BaseModel):
    query: str
    session_id: str | None = None


class SessionRequest(BaseModel):
    session_id: str


class ConfigUpdate(BaseModel):
    key: str
    value: str


# ============ API Endpoints ============

@app.get("/api/projects")
def list_projects():
    projects = get_project_list()
    result = []

    # 从全局数据库获取价格配置
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
                # Token 统计（兼容旧数据库无 token_usage 表）
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

            # 计算费用
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

    # 计算总费用
    total_input_cost = (total_input_tokens / 1000) * input_price
    total_output_cost = (total_output_tokens / 1000) * output_price

    return {
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
    }


@app.get("/api/projects/{project_name}/sessions")
def get_project_sessions(project_name: str):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        raise HTTPException(404, "Project not found")
    db = Database(db_path)
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT session_id, started_at, last_active_at, is_active FROM sessions ORDER BY last_active_at DESC"
        ).fetchall()
    return {
        "sessions": [
            {
                "session_id": r[0],
                "started_at": r[1],
                "last_active_at": r[2],
                "is_active": bool(r[3]),
            }
            for r in rows
        ]
    }


@app.get("/api/projects/{project_name}/messages")
def get_project_messages(project_name: str, session_id: str | None = None, limit: int = 100):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        raise HTTPException(404, "Project not found")
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
    return {
        "messages": [
            {
                "id": r[0],
                "session_id": r[1],
                "role": r[2],
                "content": r[3],
                "timestamp": r[4],
                "is_summarized": bool(r[5]),
            }
            for r in rows
        ]
    }


@app.get("/api/projects/{project_name}/summaries")
def get_project_summaries(project_name: str):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        raise HTTPException(404, "Project not found")
    db = Database(db_path)
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT id, session_id, summary_text, message_count, created_at FROM summaries ORDER BY id DESC"
        ).fetchall()
    return {
        "summaries": [
            {
                "id": r[0],
                "session_id": r[1],
                "summary_text": r[2],
                "message_count": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]
    }


@app.get("/api/projects/{project_name}/context")
def get_project_context(project_name: str, session_id: str | None = None):
    db_path = PROJECTS_DIR / f"{project_name}.db"
    if not db_path.exists():
        raise HTTPException(404, "Project not found")
    manager = get_manager(db_path)
    if not session_id:
        db = Database(db_path)
        with db._connect() as conn:
            row = conn.execute(
                "SELECT session_id FROM sessions ORDER BY last_active_at DESC LIMIT 1"
            ).fetchone()
        session_id = row[0] if row else f"{project_name}-session"
    return manager.get_context(session_id)


@app.get("/api/global/messages")
def get_global_messages(limit: int = 100):
    if not GLOBAL_DB.exists():
        return {"messages": []}
    db = Database(GLOBAL_DB)
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT id, session_id, role, content, timestamp, is_summarized FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {
        "messages": [
            {
                "id": r[0],
                "session_id": r[1],
                "role": r[2],
                "content": r[3],
                "timestamp": r[4],
                "is_summarized": bool(r[5]),
            }
            for r in rows
        ]
    }


@app.get("/api/global/search")
def search_global(query: str, limit: int = 50):
    if not GLOBAL_DB.exists():
        return {"results": []}
    manager = get_manager(GLOBAL_DB)
    results = manager.search_memory(query)
    return {
        "results": [
            {
                "id": m.id,
                "session_id": m.session_id,
                "role": m.role,
                "content": m.content,
                "timestamp": m.timestamp.isoformat() if m.timestamp else None,
            }
            for m in results[:limit]
        ]
    }


@app.get("/api/config")
def get_config():
    if not GLOBAL_DB.exists():
        return {"config": {}}
    db = Database(GLOBAL_DB)
    with db._connect() as conn:
        rows = conn.execute("SELECT key, value FROM config").fetchall()
    return {"config": {r[0]: r[1] for r in rows}}


@app.post("/api/config")
def update_config(req: ConfigUpdate):
    db = Database(GLOBAL_DB)
    db.set_config(req.key, req.value)
    return {"status": "ok"}


@app.get("/api/logs")
def get_logs(lines: int = 100):
    log_file = MEMORY_BASE / "hooks.log"
    if not log_file.exists():
        return {"logs": []}
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    return {"logs": all_lines[-lines:]}


@app.get("/api/token-usage")
def get_token_usage(project_name: str | None = None):
    if project_name:
        db_path = PROJECTS_DIR / f"{project_name}.db"
        if not db_path.exists():
            raise HTTPException(404, "Project not found")
    else:
        db_path = GLOBAL_DB
        if not db_path.exists():
            return {"stats": {"total_input_tokens": 0, "total_output_tokens": 0, "request_count": 0}, "history": [], "cost": {"input_cost": 0, "output_cost": 0, "total_cost": 0}}

    db = Database(db_path)
    stats = db.get_token_usage_stats()
    history = db.get_token_usage_history(limit=50)

    input_price = float(db.get_config("input_token_price", "0.003"))
    output_price = float(db.get_config("output_token_price", "0.015"))

    input_cost = (stats["total_input_tokens"] / 1000) * input_price
    output_cost = (stats["total_output_tokens"] / 1000) * output_price

    return {
        "stats": stats,
        "history": [
            {
                "id": u.id,
                "session_id": u.session_id,
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "model": u.model,
                "timestamp": u.timestamp.isoformat() if u.timestamp else None,
            }
            for u in history
        ],
        "cost": {
            "input_cost": round(input_cost, 4),
            "output_cost": round(output_cost, 4),
            "total_cost": round(input_cost + output_cost, 4),
            "input_price_per_1k": input_price,
            "output_price_per_1k": output_price,
        }
    }


@app.get("/health")
def health():
    return {"status": "ok"}


# ============ Web UI ============

@app.get("/", response_class=HTMLResponse)
def web_ui():
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
        h1 { color: #00d9ff; margin-bottom: 20px; }
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
        .message.system { background: #533483; border-left: 3px solid #9d4edd; }
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
        .log-line { font-family: monospace; font-size: 0.8em; padding: 2px 0; border-bottom: 1px solid #333; }
        .log-line.error { color: #e94560; }
        .log-line.info { color: #00d9ff; }
        .log-line.debug { color: #888; }
        .search-box { display: flex; gap: 10px; margin-bottom: 15px; }
        .search-box input { flex: 1; }
        .refresh-btn { position: fixed; bottom: 20px; right: 20px; width: 50px; height: 50px; border-radius: 50%; font-size: 1.5em; }
        .context-preview { background: #0f3460; padding: 15px; border-radius: 8px; margin: 10px 0; }
        .context-section { margin: 10px 0; }
        .context-label { color: #00d9ff; font-weight: bold; margin-bottom: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🧠 Hybrid Memory Dashboard</h1>

        <div class="tabs">
            <button class="tab active" onclick="showPanel('overview')">Overview</button>
            <button class="tab" onclick="showPanel('projects')">Projects</button>
            <button class="tab" onclick="showPanel('messages')">Messages</button>
            <button class="tab" onclick="showPanel('summaries')">Summaries</button>
            <button class="tab" onclick="showPanel('context')">Injected Context</button>
            <button class="tab" onclick="showPanel('search')">Search</button>
            <button class="tab" onclick="showPanel('token-usage')">Token Usage</button>
            <button class="tab" onclick="showPanel('logs')">Logs</button>
            <button class="tab" onclick="showPanel('config')">Config</button>
        </div>

        <!-- Overview Panel -->
        <div id="overview" class="panel active">
            <h2>System Overview</h2>
            <div class="grid" id="stats"></div>
        </div>

        <!-- Projects Panel -->
        <div id="projects" class="panel">
            <h2>Projects</h2>
            <div id="project-list" class="grid"></div>
        </div>

        <!-- Messages Panel -->
        <div id="messages" class="panel">
            <h2>Recent Messages</h2>
            <div style="margin-bottom: 15px; display: flex; gap: 10px; align-items: center;">
                <select id="msg-project" onchange="loadMessages()">
                    <option value="">-- Select Project --</option>
                </select>
                <select id="msg-session" onchange="loadMessages()">
                    <option value="">All Sessions</option>
                </select>
                <button onclick="loadMessages()">Refresh</button>
            </div>
            <div id="message-list"></div>
        </div>

        <!-- Summaries Panel -->
        <div id="summaries" class="panel">
            <h2>Summaries</h2>
            <select id="sum-project" onchange="loadSummaries()">
                <option value="">-- Select Project --</option>
            </select>
            <div id="summary-list"></div>
        </div>

        <!-- Context Panel -->
        <div id="context" class="panel">
            <h2>Injected Context Preview</h2>
            <p style="color: #888; margin-bottom: 15px;">This shows what would be injected into Claude when starting a session.</p>
            <select id="ctx-project" onchange="loadContext()">
                <option value="">-- Select Project --</option>
            </select>
            <div id="context-preview"></div>
        </div>

        <!-- Search Panel -->
        <div id="search" class="panel">
            <h2>Search Global Memory</h2>
            <div class="search-box">
                <input type="text" id="search-query" placeholder="Search messages...">
                <button onclick="doSearch()">Search</button>
            </div>
            <div id="search-results"></div>
        </div>

        <!-- Token Usage Panel -->
        <div id="token-usage" class="panel">
            <h2>Token Usage & Cost</h2>
            <div class="grid" id="token-stats"></div>
            <h3 style="margin: 20px 0 10px; color: #00d9ff;">Recent Usage History</h3>
            <div id="token-history"></div>
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
                <h3 style="margin-bottom: 10px;">Current Settings</h3>
                <div id="config-list"></div>
                <h3 style="margin: 20px 0 10px;">Add/Update Config</h3>
                <input type="text" id="config-key" placeholder="Key">
                <input type="text" id="config-value" placeholder="Value">
                <button onclick="saveConfig()">Save</button>
            </div>
        </div>
    </div>

    <button class="refresh-btn" onclick="refreshAll()">↻</button>

    <script>
        let projects = [];
        let currentPanel = 'overview';
        let autoRefreshEnabled = true;

        function showPanel(id) {
            document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.getElementById(id).classList.add('active');
            event.target.classList.add('active');
            currentPanel = id;
        }

        async function loadProjects() {
            const res = await fetch('/api/projects');
            const data = await res.json();
            projects = data.projects;

            // Update stats with token usage
            let totalMsgs = 0, totalSums = 0;
            projects.forEach(p => { totalMsgs += p.messages || 0; totalSums += p.summaries || 0; });

            const tokenRes = await fetch('/api/token-usage');
            const tokenData = await tokenRes.json();
            const cost = tokenData.cost;

            document.getElementById('stats').innerHTML = `
                <div class="card stat"><div class="stat-value">${projects.length}</div><div class="stat-label">Projects</div></div>
                <div class="card stat"><div class="stat-value">${totalMsgs}</div><div class="stat-label">Total Messages</div></div>
                <div class="card stat"><div class="stat-value">${totalSums}</div><div class="stat-label">Summaries</div></div>
                <div class="card stat"><div class="stat-value">${formatNumber(tokenData.stats.total_input_tokens)}</div><div class="stat-label">Input Tokens</div></div>
                <div class="card stat"><div class="stat-value">${formatNumber(tokenData.stats.total_output_tokens)}</div><div class="stat-label">Output Tokens</div></div>
                <div class="card stat" style="background: #1f4068;"><div class="stat-value" style="color: #e94560;">$${cost.total_cost.toFixed(4)}</div><div class="stat-label">Total Cost</div></div>
            `;

            // Update project list
            document.getElementById('project-list').innerHTML = projects.map(p => `
                <div class="card">
                    <div class="card-header">
                        <strong>${p.name}</strong>
                        <span class="badge">${p.messages || 0} msgs</span>
                    </div>
                    <div>Sessions: ${p.sessions || 0} | Summaries: ${p.summaries || 0}</div>
                </div>
            `).join('');

            // Update dropdowns
            const opts = projects.map(p => `<option value="${p.name}">${p.name}</option>`).join('');
            document.getElementById('msg-project').innerHTML = '<option value="">-- Select Project --</option>' + opts;
            document.getElementById('sum-project').innerHTML = '<option value="">-- Select Project --</option>' + opts;
            document.getElementById('ctx-project').innerHTML = '<option value="">-- Select Project --</option>' + opts;
        }

        async function loadSessions(project) {
            if (!project) return;
            const res = await fetch(`/api/projects/${project}/sessions`);
            const data = await res.json();
            const opts = data.sessions.map(s => `<option value="${s.session_id}">${s.session_id.substring(0,20)}...</option>`).join('');
            document.getElementById('msg-session').innerHTML = '<option value="">All Sessions</option>' + opts;
        }

        async function loadMessages() {
            const project = document.getElementById('msg-project').value;
            if (!project) return;
            await loadSessions(project);
            const session = document.getElementById('msg-session').value;
            const url = session ? `/api/projects/${project}/messages?session_id=${session}` : `/api/projects/${project}/messages`;
            const res = await fetch(url);
            const data = await res.json();
            document.getElementById('message-list').innerHTML = data.messages.map(m => `
                <div class="message ${m.role}">
                    <div class="message-role">${m.role}</div>
                    <div class="message-content">${escapeHtml(m.content)}</div>
                    <div class="message-meta">${m.timestamp} | ${m.is_summarized ? '✓ Summarized' : 'Not summarized'}</div>
                </div>
            `).join('') || '<p>No messages found</p>';
        }

        async function loadSummaries() {
            const project = document.getElementById('sum-project').value;
            if (!project) return;
            const res = await fetch(`/api/projects/${project}/summaries`);
            const data = await res.json();
            document.getElementById('summary-list').innerHTML = data.summaries.map(s => `
                <div class="summary">
                    <div style="margin-bottom: 10px;"><strong>Session:</strong> ${s.session_id} | <strong>Messages:</strong> ${s.message_count} | <strong>Created:</strong> ${s.created_at}</div>
                    <div class="summary-text">${escapeHtml(s.summary_text)}</div>
                </div>
            `).join('') || '<p>No summaries found</p>';
        }

        async function loadContext() {
            const project = document.getElementById('ctx-project').value;
            if (!project) return;
            const res = await fetch(`/api/projects/${project}/context`);
            const data = await res.json();
            let html = '<div class="context-preview">';
            if (data.summaries) {
                html += `<div class="context-section"><div class="context-label">📝 Historical Summaries:</div><div class="summary-text">${escapeHtml(data.summaries)}</div></div>`;
            }
            if (data.messages && data.messages.length > 0) {
                html += `<div class="context-section"><div class="context-label">💬 Recent Messages (${data.messages.length}):</div>`;
                data.messages.forEach(m => {
                    html += `<div class="message ${m.role}"><div class="message-role">${m.role}</div><div class="message-content">${escapeHtml(m.content)}</div></div>`;
                });
                html += '</div>';
            }
            if (!data.summaries && (!data.messages || data.messages.length === 0)) {
                html += '<p style="color: #888;">No context available for this project yet.</p>';
            }
            html += '</div>';
            document.getElementById('context-preview').innerHTML = html;
        }

        async function doSearch() {
            const query = document.getElementById('search-query').value;
            if (!query) return;
            const res = await fetch(`/api/global/search?query=${encodeURIComponent(query)}`);
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
            document.getElementById('log-list').innerHTML = data.logs.map(line => {
                let cls = 'log-line';
                if (line.includes('ERROR')) cls += ' error';
                else if (line.includes('INFO')) cls += ' info';
                else if (line.includes('DEBUG')) cls += ' debug';
                return `<div class="${cls}">${escapeHtml(line)}</div>`;
            }).join('');
        }

        async function loadConfig() {
            const res = await fetch('/api/config');
            const data = await res.json();
            const items = Object.entries(data.config);
            document.getElementById('config-list').innerHTML = items.length > 0
                ? items.map(([k, v]) => `<div><strong>${k}:</strong> ${v}</div>`).join('')
                : '<p style="color: #888;">No configuration set</p>';
        }

        async function loadTokenUsage() {
            const res = await fetch('/api/token-usage');
            const data = await res.json();
            const stats = data.stats;
            const cost = data.cost;

            document.getElementById('token-stats').innerHTML = `
                <div class="card stat"><div class="stat-value">${formatNumber(stats.total_input_tokens)}</div><div class="stat-label">Input Tokens</div></div>
                <div class="card stat"><div class="stat-value">${formatNumber(stats.total_output_tokens)}</div><div class="stat-label">Output Tokens</div></div>
                <div class="card stat"><div class="stat-value">${stats.request_count}</div><div class="stat-label">Requests</div></div>
                <div class="card stat"><div class="stat-value">$${cost.input_cost.toFixed(4)}</div><div class="stat-label">Input Cost</div></div>
                <div class="card stat"><div class="stat-value">$${cost.output_cost.toFixed(4)}</div><div class="stat-label">Output Cost</div></div>
                <div class="card stat" style="background: #1f4068;"><div class="stat-value" style="color: #e94560;">$${cost.total_cost.toFixed(4)}</div><div class="stat-label">Total Cost</div></div>
            `;

            document.getElementById('token-history').innerHTML = data.history.length > 0
                ? `<table style="width: 100%; border-collapse: collapse;">
                    <tr style="border-bottom: 1px solid #333;">
                        <th style="text-align: left; padding: 8px;">Time</th>
                        <th style="text-align: right; padding: 8px;">Input</th>
                        <th style="text-align: right; padding: 8px;">Output</th>
                        <th style="text-align: left; padding: 8px;">Model</th>
                    </tr>
                    ${data.history.map(u => `
                        <tr style="border-bottom: 1px solid #222;">
                            <td style="padding: 8px;">${new Date(u.timestamp).toLocaleString()}</td>
                            <td style="text-align: right; padding: 8px;">${formatNumber(u.input_tokens)}</td>
                            <td style="text-align: right; padding: 8px;">${formatNumber(u.output_tokens)}</td>
                            <td style="padding: 8px; color: #888;">${u.model || '-'}</td>
                        </tr>
                    `).join('')}
                </table>`
                : '<p style="color: #888;">No usage data yet</p>';
        }

        function formatNumber(num) {
            if (num >= 1000000) return (num / 1000000).toFixed(2) + 'M';
            if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
            return num.toString();
        }

        async function saveConfig() {
            const key = document.getElementById('config-key').value;
            const value = document.getElementById('config-value').value;
            if (!key) return;
            await fetch('/api/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({key, value})
            });
            loadConfig();
            document.getElementById('config-key').value = '';
            document.getElementById('config-value').value = '';
        }

        function escapeHtml(text) {
            if (!text) return '';
            return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        async function refreshAll() {
            await loadProjects();
            loadConfig();
            loadTokenUsage();
        }

        async function autoRefresh() {
            // Save scroll position before refresh
            const scrollTop = window.scrollY;

            await loadProjects();
            loadConfig();
            loadTokenUsage();

            // Restore scroll position after refresh
            window.scrollTo(0, scrollTop);
        }

        // Auto-refresh every 5 seconds with scroll position preservation
        setInterval(autoRefresh, 5000);

        // Initial load
        refreshAll();
    </script>
</body>
</html>"""


def main():
    import uvicorn
    logger.info("Starting Hybrid Memory Dashboard on http://localhost:37888")
    print("\n" + "="*50)
    print("🧠 Hybrid Memory Dashboard")
    print("="*50)
    print(f"Open in browser: http://localhost:37888")
    print("="*50 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=37888, log_level="warning")


if __name__ == "__main__":
    main()
