#!/usr/bin/env python3
"""
SessionStart Hook - 双层记忆系统
- 项目级记忆：每个项目独立的 .db 文件
- 全局记忆：所有项目共享的 .db 文件
"""
import sys
import json
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger
import httpx
from src.memory_core import MemoryManager, publish_event, ContentConfig, process_content
from src.memory_core.config import load_config
from src.memory_core.hook_utils import (
    MEMORY_BASE, GLOBAL_DB, setup_hook_logger, configure_utf8_stdio,
    get_project_name, get_project_db_path
)

setup_hook_logger()


def start_dashboard_if_not_running():
    """如果 Dashboard 没运行，就后台启动它"""
    pid_file = MEMORY_BASE / "dashboard.pid"

    def is_dashboard_running():
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                if sys.platform == "win32":
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(0x1000, False, pid)
                    if handle:
                        kernel32.CloseHandle(handle)
                        try:
                            resp = httpx.get("http://localhost:37888/health", timeout=2.0)
                            if resp.status_code == 200:
                                return True
                        except:
                            pass
                else:
                    import os
                    try:
                        os.kill(pid, 0)
                        try:
                            resp = httpx.get("http://localhost:37888/health", timeout=2.0)
                            if resp.status_code == 200:
                                return True
                        except:
                            pass
                    except OSError:
                        pass
            except:
                pass
            pid_file.unlink(missing_ok=True)

        try:
            resp = httpx.get("http://localhost:37888/health", timeout=2.0)
            return resp.status_code == 200
        except:
            return False

    try:
        if is_dashboard_running():
            logger.debug("Dashboard already running")
            return

        dashboard_script = MEMORY_BASE.parent / "src" / "http_api" / "dashboard.py"
        if dashboard_script.exists():
            if sys.platform == "win32":
                venv_python = MEMORY_BASE.parent / ".venv" / "Scripts" / "python.exe"
            else:
                venv_python = MEMORY_BASE.parent / ".venv" / "bin" / "python"

            python_cmd = str(venv_python) if venv_python.exists() else "python"
            logger.debug(f"Starting dashboard with: {python_cmd}")

            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            proc = subprocess.Popen(
                [python_cmd, str(dashboard_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                cwd=str(MEMORY_BASE.parent)
            )
            pid_file.write_text(str(proc.pid))
            logger.info(f"Dashboard started at http://localhost:37888 (PID: {proc.pid})")
    except Exception as e:
        logger.debug(f"Dashboard check failed: {e}")


def main():
    logger.info("=" * 50)
    logger.info("Hook sessionStart triggered")
    start_dashboard_if_not_running()
    configure_utf8_stdio()

    try:
        raw_input = sys.stdin.read()
        logger.debug(f"Raw input: {raw_input[:500] if raw_input else 'empty'}")
        input_data = json.loads(raw_input) if raw_input.strip() else {}
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        input_data = {}

    project_name = get_project_name()
    session_id = input_data.get("session_id") or f"{project_name}-session"

    logger.info(f"Project: {project_name}")
    logger.info(f"Session ID: {session_id}")
    logger.info(f"Global DB: {GLOBAL_DB}")
    logger.info(f"Project DB: {get_project_db_path(project_name)}")

    publish_event("session", f"Session started: {project_name}", session_id)

    output_parts = []

    try:
        config_mgr = load_config(GLOBAL_DB)
        config_kwargs = config_mgr.get_memory_manager_kwargs()
        logger.info(f"Loaded config: window={config_kwargs['short_term_window_size']}, threshold={config_kwargs['summary_trigger_threshold']}, llm={config_kwargs['llm_provider']}")

        inject_summary_count = config_mgr.get_int("inject_summary_count")
        inject_recent_count = config_mgr.get_int("inject_recent_count")
        inject_knowledge_count = config_mgr.get_int("inject_knowledge_count")
        logger.info(f"Inject config: summaries={inject_summary_count}, recent={inject_recent_count}")

        # 1. 加载项目级记忆
        project_db = get_project_db_path(project_name)
        project_manager = MemoryManager(db_path=project_db, **config_kwargs)
        project_manager.start_session(session_id)

        project_context = project_manager.get_context(session_id)

        if not project_context["summaries"] and not project_context["messages"]:
            auto_summaries = project_manager.db.get_all_summaries(limit=inject_summary_count)
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
                    s = project_manager.db.get_summary_by_id(sid)
                    if s:
                        extra_summaries.append(s)

            summaries = extra_summaries + list(reversed(auto_summaries))
            logger.info(f"Injecting summaries: {len(extra_summaries)} extra + {len(auto_summaries)} auto (old to new)")

            recent_messages = project_manager.db.get_recent_messages_all_sessions(limit=inject_recent_count)

            if summaries:
                project_context["summaries"] = "\n\n---\n\n".join(s.summary_text for s in summaries)
            if recent_messages:
                project_context["messages"] = [{"role": m.role, "content": m.content} for m in recent_messages]

        if project_context["summaries"] or project_context["messages"]:
            logger.info(f"Found project context: summaries={len(project_context['summaries'])} chars, messages={len(project_context['messages'])}")
            if project_context["summaries"]:
                output_parts.append(f"# [{project_name}] 项目历史摘要:\n{project_context['summaries']}")
            if project_context["messages"]:
                content_config = ContentConfig(
                    include_thinking=config_mgr.get("content_include_thinking").lower() == "true",
                    include_tool=config_mgr.get("content_include_tool").lower() == "true",
                    include_text=config_mgr.get("content_include_text").lower() == "true",
                    max_chars_thinking=config_mgr.get_int("content_max_chars_thinking"),
                    max_chars_tool=config_mgr.get_int("content_max_chars_tool"),
                    max_chars_text=config_mgr.get_int("content_max_chars_text"),
                )
                formatted_messages = []
                for m in project_context["messages"]:
                    processed = process_content(m['content'], content_config)
                    if not processed:
                        continue
                    formatted_messages.append(f"- {m['role']}: {processed}")
                recent = "\n".join(formatted_messages)
                output_parts.append(f"# [{project_name}] 最近对话:\n{recent}")

        # 2. 加载结构化知识（项目级长期记忆）
        knowledge = project_manager.get_knowledge(None)
        knowledge_items = []
        if knowledge.get("user_preferences"):
            knowledge_items.append(f"用户偏好: {', '.join(knowledge['user_preferences'][:inject_knowledge_count])}")
        if knowledge.get("architecture_decisions"):
            knowledge_items.append(f"架构决策: {', '.join(knowledge['architecture_decisions'][:inject_knowledge_count])}")
        if knowledge.get("design_principles"):
            knowledge_items.append(f"设计原则: {', '.join(knowledge['design_principles'][:inject_knowledge_count])}")
        if knowledge.get("learned_patterns"):
            knowledge_items.append(f"行为模式: {', '.join(knowledge['learned_patterns'][:inject_knowledge_count])}")
        # 兼容旧数据
        if knowledge.get("project_decisions"):
            knowledge_items.append(f"项目决策: {', '.join(knowledge['project_decisions'][:inject_knowledge_count])}")
        if knowledge.get("key_facts"):
            knowledge_items.append(f"关键事实: {', '.join(knowledge['key_facts'][:inject_knowledge_count])}")

        if knowledge_items:
            output_parts.append(f"# [{project_name}] 累积知识:\n" + "\n".join(f"- {item}" for item in knowledge_items))
            logger.info(f"Found knowledge: {len(knowledge_items)} categories")

        # 2.5 加载已确认的决策：按 timestamp DESC 取最新 N 个（与 Dashboard JS 一致）
        inject_decision_count = config_mgr.get_int("inject_decision_count")
        all_confirmed = project_manager.db.get_decisions(project=project_name, status="confirmed", limit=1000)
        sorted_confirmed = sorted(all_confirmed, key=lambda d: d.timestamp, reverse=True)
        auto_decisions = sorted_confirmed[:inject_decision_count]
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
                d = project_manager.db.get_decision_by_id(did)
                if d and d.status == "confirmed":
                    extra_decisions.append(d)

        all_decisions = extra_decisions + list(reversed(auto_decisions))
        if all_decisions:
            decision_lines = []
            for d in all_decisions:
                line = f"- **{d.problem}** → {d.solution}"
                if d.reason:
                    line += f" (因为: {d.reason})"
                files = json.loads(d.files) if isinstance(d.files, str) else (d.files or [])
                if files:
                    line += f" [文件: {', '.join(files)}]"
                decision_lines.append(line)
            output_parts.append(f"# [{project_name}] 相关决策:\n" + "\n".join(decision_lines))
            logger.info(f"Injecting {len(all_decisions)} confirmed decisions (auto={len(auto_decisions)}, extra={len(extra_decisions)})")

        # 3. 加载全局记忆
        global_manager = MemoryManager(db_path=GLOBAL_DB, **config_kwargs)
        global_manager.start_session(f"global-{session_id}")

    except Exception as e:
        logger.error(f"Error loading context: {e}")
        import traceback
        logger.error(traceback.format_exc())

    if output_parts:
        context_text = "\n\n".join(output_parts)
        result = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context_text
            }
        }
        logger.info(f"Returning context to Claude: {len(context_text)} chars")
    else:
        result = {}
        logger.info("No previous context found")

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
