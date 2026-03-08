#!/usr/bin/env python3
"""
SessionStart Hook - 双层记忆系统
- 项目级记忆：每个项目独立的 .db 文件
- 全局记忆：所有项目共享的 .db 文件
"""
import sys
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger
from src.memory_core import MemoryManager, publish_event
from src.memory_core.config import load_config

# 路径配置
MEMORY_BASE = Path(__file__).parent.parent.parent / "data"
GLOBAL_DB = MEMORY_BASE / "global_memory.db"
PROJECTS_DIR = MEMORY_BASE / "projects"

LOG_FILE = MEMORY_BASE / "hooks.log"
MEMORY_BASE.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# 移除默认的 stderr handler，只输出到文件
logger.remove()
logger.add(LOG_FILE, level="DEBUG", rotation="1 MB")


def get_project_name() -> str:
    """从当前工作目录获取项目名"""
    cwd = os.getcwd()
    return Path(cwd).name


def get_project_db_path(project_name: str) -> Path:
    """获取项目级数据库路径"""
    return PROJECTS_DIR / f"{project_name}.db"


def start_dashboard_if_not_running():
    """如果 Dashboard 没运行，就后台启动它（使用 PID 文件和 HTTP 健康检查）"""
    import subprocess
    import httpx

    pid_file = MEMORY_BASE / "dashboard.pid"

    def is_dashboard_running():
        # 方法1: 检查 PID 文件
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                # 检查进程是否存在
                if sys.platform == "win32":
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                    if handle:
                        kernel32.CloseHandle(handle)
                        # 进程存在，再检查 HTTP
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
                        # 进程存在，再检查 HTTP
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
            # PID 文件无效，删除它
            pid_file.unlink(missing_ok=True)

        # 方法2: 直接 HTTP 健康检查
        try:
            resp = httpx.get("http://localhost:37888/health", timeout=2.0)
            return resp.status_code == 200
        except:
            return False

    try:
        if is_dashboard_running():
            logger.debug("Dashboard already running")
            return

        # Dashboard 没运行，启动它
        dashboard_script = MEMORY_BASE.parent / "src" / "http_api" / "dashboard.py"
        if dashboard_script.exists():
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            proc = subprocess.Popen(
                ["python", str(dashboard_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags
            )
            # 保存 PID
            pid_file.write_text(str(proc.pid))
            logger.info(f"Dashboard started at http://localhost:37888 (PID: {proc.pid})")
    except Exception as e:
        logger.debug(f"Dashboard check failed: {e}")


def main():
    logger.info("=" * 50)
    logger.info("Hook sessionStart triggered")
    start_dashboard_if_not_running()

    # Windows UTF-8 fix
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    try:
        raw_input = sys.stdin.read()
        logger.debug(f"Raw input: {raw_input[:500] if raw_input else 'empty'}")
        input_data = json.loads(raw_input) if raw_input.strip() else {}
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        input_data = {}

    # 获取项目名和会话ID
    project_name = get_project_name()
    session_id = input_data.get("session_id") or f"{project_name}-session"

    logger.info(f"Project: {project_name}")
    logger.info(f"Session ID: {session_id}")
    logger.info(f"Global DB: {GLOBAL_DB}")
    logger.info(f"Project DB: {get_project_db_path(project_name)}")

    publish_event("session", f"Session started: {project_name}", session_id)

    output_parts = []

    try:
        # 从全局数据库加载配置
        config_mgr = load_config(GLOBAL_DB)
        config_kwargs = config_mgr.get_memory_manager_kwargs()
        logger.info(f"Loaded config: window={config_kwargs['short_term_window_size']}, threshold={config_kwargs['summary_trigger_threshold']}, llm={config_kwargs['llm_provider']}")

        # 读取注入相关配置
        inject_summary_count = config_mgr.get_int("inject_summary_count")
        inject_recent_count = config_mgr.get_int("inject_recent_count")
        inject_preview_length = config_mgr.get_int("inject_preview_length")
        inject_knowledge_count = config_mgr.get_int("inject_knowledge_count")
        inject_task_count = config_mgr.get_int("inject_task_count")
        logger.info(f"Inject config: summaries={inject_summary_count}, recent={inject_recent_count}, preview_len={inject_preview_length}")

        # 1. 加载项目级记忆（跨会话）
        project_db = get_project_db_path(project_name)
        project_manager = MemoryManager(db_path=project_db, **config_kwargs)
        project_manager.start_session(session_id)

        # 先尝试当前会话，如果没有则获取所有会话的上下文
        project_context = project_manager.get_context(session_id)

        # 如果当前会话没有内容，获取所有会话的摘要和最近消息
        if not project_context["summaries"] and not project_context["messages"]:
            # 获取最新 N 条（自动）
            auto_summaries = project_manager.db.get_all_summaries(limit=inject_summary_count)
            auto_ids = {s.id for s in auto_summaries}

            # 获取额外选中的
            selected_config = config_mgr.get("selected_summary_ids")
            try:
                selected_map = json.loads(selected_config) if selected_config else {}
            except json.JSONDecodeError:
                selected_map = {}
            extra_ids = selected_map.get(project_name, [])

            # 合并：extra + auto（去重），手动选的在上，最新的在下
            extra_summaries = []
            for sid in extra_ids:
                if sid not in auto_ids:
                    s = project_manager.db.get_summary_by_id(sid)
                    if s:
                        extra_summaries.append(s)

            # 顺序：extra（旧的背景）在上，auto（最新的）在下
            # auto_summaries 是 DESC 排序（最新在前），需要反转为旧到新
            summaries = extra_summaries + list(reversed(auto_summaries))
            logger.info(f"Injecting summaries: {len(extra_summaries)} extra + {len(auto_summaries)} auto (old to new)")

            recent_messages = project_manager.db.get_recent_messages_all_sessions(limit=inject_recent_count)

            if summaries:
                project_context["summaries"] = "\n\n---\n\n".join(s.summary_text for s in summaries)
            if recent_messages:
                project_context["messages"] = [{"role": m.role, "content": m.content} for m in reversed(recent_messages)]

        if project_context["summaries"] or project_context["messages"]:
            logger.info(f"Found project context: summaries={len(project_context['summaries'])} chars, messages={len(project_context['messages'])}")
            if project_context["summaries"]:
                output_parts.append(f"# [{project_name}] 项目历史摘要:\n{project_context['summaries']}")
            if project_context["messages"]:
                def truncate(text, max_len):
                    if max_len <= 0 or len(text) <= max_len:
                        return text
                    return text[:max_len] + "..."
                recent = "\n".join(f"- {m['role']}: {truncate(m['content'], inject_preview_length)}" for m in project_context["messages"])
                output_parts.append(f"# [{project_name}] 最近对话:\n{recent}")

        # 2. 加载结构化知识（所有会话，全部 6 类）
        knowledge = project_manager.get_knowledge(None)  # None = 获取所有会话的知识
        knowledge_items = []
        if knowledge.get("user_preferences"):
            knowledge_items.append(f"用户偏好: {', '.join(knowledge['user_preferences'][:inject_knowledge_count])}")
        if knowledge.get("project_decisions"):
            knowledge_items.append(f"项目决策: {', '.join(knowledge['project_decisions'][:inject_knowledge_count])}")
        if knowledge.get("key_facts"):
            knowledge_items.append(f"关键事实: {', '.join(knowledge['key_facts'][:inject_knowledge_count])}")
        if knowledge.get("pending_tasks"):
            knowledge_items.append(f"待办事项: {', '.join(knowledge['pending_tasks'][:inject_task_count])}")
        if knowledge.get("learned_patterns"):
            knowledge_items.append(f"行为模式: {', '.join(knowledge['learned_patterns'][:inject_knowledge_count])}")
        if knowledge.get("important_context"):
            knowledge_items.append(f"重要上下文: {', '.join(knowledge['important_context'][:inject_knowledge_count])}")

        if knowledge_items:
            output_parts.append(f"# [{project_name}] 累积知识:\n" + "\n".join(f"- {item}" for item in knowledge_items))
            logger.info(f"Found knowledge: {len(knowledge_items)} categories")

        # 3. 加载全局记忆（搜索相关内容）
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
