#!/usr/bin/env python3
"""
Background Summary Script - 后台执行总结和知识提取

由 stop.py hook 启动，在独立进程中执行，避免阻塞 Claude Code。
"""
import sys
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger
from src.memory_core import MemoryManager, publish_event
from src.memory_core.config import load_config

# 路径配置
MEMORY_BASE = Path(__file__).parent.parent.parent / "data"
LOG_FILE = MEMORY_BASE / "hooks.log"
MEMORY_BASE.mkdir(parents=True, exist_ok=True)

# 配置日志
logger.remove()
logger.add(LOG_FILE, level="DEBUG", rotation="1 MB", retention="1 hour")


def main():
    if len(sys.argv) < 5:
        logger.error(f"Usage: {sys.argv[0]} <project_name> <session_id> <project_db> <global_db> [--no-end-session]")
        sys.exit(1)

    project_name = sys.argv[1]
    session_id = sys.argv[2]
    project_db = Path(sys.argv[3])
    global_db = Path(sys.argv[4])
    no_end_session = "--no-end-session" in sys.argv
    global_session_id = f"{project_name}:{session_id}"

    if no_end_session:
        logger.info(f"[Background] Starting summary (no end_session) for project={project_name}, session={session_id}")
    else:
        logger.info(f"[Background] Starting end_session for project={project_name}, session={session_id}")

    try:
        # 加载配置
        config_mgr = load_config(global_db)
        config_kwargs = config_mgr.get_memory_manager_kwargs()

        project_manager = MemoryManager(db_path=project_db, **config_kwargs)
        global_manager = MemoryManager(db_path=global_db, **config_kwargs)

        if no_end_session:
            # 只触发总结，不结束会话
            project_summary = project_manager.trigger_summary(session_id)
            if project_summary:
                logger.info(f"[Background][Project] Summary created: id={project_summary.id}")
                publish_event("summary_done", f"Summary created: #{project_summary.id}", project_name)
            else:
                logger.info(f"[Background][Project] No summary needed")

            global_summary = global_manager.trigger_summary(global_session_id)
            if global_summary:
                logger.info(f"[Background][Global] Summary created: id={global_summary.id}")
            else:
                logger.info(f"[Background][Global] No summary needed")
        else:
            # 结束会话（包含总结）
            project_summary = project_manager.end_session(session_id)
            if project_summary:
                logger.info(f"[Background][Project] Session ended with summary: id={project_summary.id}")
                publish_event("summary_done", f"Summary created: #{project_summary.id}", project_name)
            else:
                logger.info(f"[Background][Project] Session ended without summary")

            global_summary = global_manager.end_session(global_session_id)
            if global_summary:
                logger.info(f"[Background][Global] Session ended with summary: id={global_summary.id}")
            else:
                logger.info(f"[Background][Global] Session ended without summary")

        logger.info(f"[Background] Processing completed for {project_name}")
        publish_event("background_done", f"Background processing completed", project_name)

    except Exception as e:
        logger.error(f"[Background] Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        publish_event("error", f"Background summary failed: {e}", project_name)


if __name__ == "__main__":
    main()
