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
from src.memory_core import MemoryManager, publish_event, DecisionExtractor, Decision
from src.memory_core.config import load_config
from src.memory_core.database import Database
from src.memory_core.llm_client import create_llm_client

# 路径配置
MEMORY_BASE = Path(__file__).parent.parent.parent / "data"
LOG_FILE = MEMORY_BASE / "hooks.log"
MEMORY_BASE.mkdir(parents=True, exist_ok=True)

# 配置日志
logger.remove()
logger.add(LOG_FILE, level="DEBUG", rotation="1 MB", retention="1 hour")


def extract_decisions(project_name: str, session_id: str, project_db: Path, config_mgr):
    """从最近的对话中提取决策（可能多个或零个）"""
    logger.info(f"[Background] Extracting decisions for {project_name}")
    publish_event("decision", "Extracting decisions from conversation", project_name)

    db = Database(project_db)

    # 获取未进行决策提取的消息
    messages = db.get_messages_for_decision(None)
    if len(messages) < 3:
        logger.info("[Background] Not enough messages for decision extraction")
        return

    # 转换为简单格式
    msg_list = [{"role": m.role, "content": m.content} for m in messages]

    # 创建 LLM 客户端
    llm_client = create_llm_client(
        provider=config_mgr.get("llm_provider"),
        ollama_model=config_mgr.get("ollama_model"),
        ollama_base_url=config_mgr.get("ollama_base_url"),
        ollama_timeout=float(config_mgr.get("ollama_timeout") or 300),
        ollama_keep_alive=config_mgr.get("ollama_keep_alive"),
    )

    # 创建内容处理配置
    from src.memory_core.content_processor import ContentConfig
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
    decisions = extractor.extract_decisions(msg_list, project_name, session_id, message_ids=message_ids)

    # 标记消息为已决策提取
    if message_ids:
        db.mark_messages_decision_extracted(message_ids)

    if decisions:
        for decision in decisions:
            db.add_decision(decision)
        logger.info(f"[Background] Saved {len(decisions)} decisions")
        publish_event("decision_done", f"Extracted {len(decisions)} decisions", project_name)
    else:
        logger.info("[Background] No decisions detected in conversation")


def main():
    if len(sys.argv) < 5:
        logger.error(f"Usage: {sys.argv[0]} <project_name> <session_id> <project_db> <global_db> [--no-end-session]")
        sys.exit(1)

    project_name = sys.argv[1]
    session_id = sys.argv[2]
    project_db = Path(sys.argv[3])
    global_db = Path(sys.argv[4])
    no_end_session = "--no-end-session" in sys.argv
    embedding_only = "--embedding-only" in sys.argv
    global_session_id = f"{project_name}:{session_id}"

    if embedding_only:
        logger.info(f"[Background] Starting embedding-only for project={project_name}")
    elif no_end_session:
        logger.info(f"[Background] Starting summary for project={project_name}, session={session_id}")
    else:
        logger.info(f"[Background] Starting end_session for project={project_name}, session={session_id}")

    try:
        # 加载配置
        config_mgr = load_config(global_db)
        config_kwargs = config_mgr.get_memory_manager_kwargs()

        project_manager = MemoryManager(db_path=project_db, **config_kwargs)
        global_manager = MemoryManager(db_path=global_db, **config_kwargs)

        if not embedding_only:
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

        # 补充执行 embedding（hook 中跳过的）
        try:
            project_indexed = project_manager.index_pending_messages()
            global_indexed = global_manager.index_pending_messages()
            if project_indexed or global_indexed:
                logger.info(f"[Background] Indexed pending: project={project_indexed}, global={global_indexed}")
        except Exception as e:
            logger.error(f"[Background] Embedding error: {e}")

        # 决策提取（在总结或会话结束时触发，非 embedding-only）
        if not embedding_only:
            try:
                extract_decisions(project_name, session_id, project_db, config_mgr)
            except Exception as e:
                logger.error(f"[Background] Decision extraction error: {e}")

        logger.info(f"[Background] Processing completed for {project_name}")
        publish_event("background_done", f"Background processing completed", project_name)

    except Exception as e:
        logger.error(f"[Background] Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        publish_event("error", f"Background summary failed: {e}", project_name)


if __name__ == "__main__":
    main()
