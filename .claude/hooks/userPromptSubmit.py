#!/usr/bin/env python3
"""
UserPromptSubmit Hook - 双层记忆系统
同时保存用户消息到：
- 项目级数据库
- 全局数据库
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger
from src.memory_core import MemoryManager
from src.memory_core.config import load_config
from src.memory_core.hook_utils import (
    GLOBAL_DB, setup_hook_logger, configure_utf8_stdio,
    get_project_name, get_project_db_path, sanitize_text
)

setup_hook_logger()


def main():
    logger.info("Hook userPromptSubmit triggered")
    configure_utf8_stdio()

    try:
        raw_input = sys.stdin.read()
        logger.debug(f"Raw input length: {len(raw_input)}")
        input_data = json.loads(raw_input) if raw_input.strip() else {}
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        input_data = {}

    logger.debug(f"Input data keys: {list(input_data.keys())}")

    project_name = get_project_name()
    session_id = input_data.get("session_id") or f"{project_name}-session"
    prompt = input_data.get("prompt", "") or input_data.get("user_prompt", "")

    logger.info(f"Project: {project_name}, Session: {session_id}")
    logger.info(f"Prompt length: {len(prompt)}")

    if prompt:
        try:
            prompt = sanitize_text(prompt)
            logger.debug(f"Prompt preview: {prompt[:100]}...")

            config_mgr = load_config(GLOBAL_DB)
            config_kwargs = config_mgr.get_memory_manager_kwargs()

            # 1. 保存到项目级数据库
            project_db = get_project_db_path(project_name)
            project_manager = MemoryManager(db_path=project_db, **config_kwargs)
            project_msg = project_manager.add_message(session_id, "user", prompt, auto_summarize=False)
            logger.info(f"[Project] User message saved: id={project_msg.id}")

            # 2. 保存到全局数据库
            global_manager = MemoryManager(db_path=GLOBAL_DB, **config_kwargs)
            global_session_id = f"{project_name}:{session_id}"
            global_msg = global_manager.add_message(global_session_id, "user", prompt, auto_summarize=False)
            logger.info(f"[Global] User message saved: id={global_msg.id}, session={global_session_id}")

        except Exception as e:
            logger.error(f"Error saving message: {e}")
            import traceback
            logger.error(traceback.format_exc())
    else:
        logger.warning("Empty prompt, skipping save")

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
