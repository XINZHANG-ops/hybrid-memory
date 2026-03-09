#!/usr/bin/env python3
"""
UserPromptSubmit Hook - 双层记忆系统
同时保存用户消息到：
- 项目级数据库
- 全局数据库
"""
import sys
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger
from src.memory_core import MemoryManager
from src.memory_core.config import load_config

# 路径配置
MEMORY_BASE = Path(__file__).parent.parent.parent / "data"
GLOBAL_DB = MEMORY_BASE / "global_memory.db"
PROJECTS_DIR = MEMORY_BASE / "projects"

LOG_FILE = MEMORY_BASE / "hooks.log"
MEMORY_BASE.mkdir(parents=True, exist_ok=True)

# 移除默认的 stderr handler，只输出到文件
logger.remove()
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
logger.add(LOG_FILE, level="DEBUG", rotation="1 MB", retention="1 hour")


def get_project_name() -> str:
    cwd = os.getcwd()
    return Path(cwd).name


def get_project_db_path(project_name: str) -> Path:
    return PROJECTS_DIR / f"{project_name}.db"


def sanitize_text(text: str) -> str:
    """移除无效的 surrogate 字符"""
    return text.encode('utf-8', errors='surrogateescape').decode('utf-8', errors='replace')


def main():
    logger.info("Hook userPromptSubmit triggered")

    # Windows UTF-8 fix
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

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
            # 清理可能的无效字符
            prompt = sanitize_text(prompt)
            logger.debug(f"Prompt preview: {prompt[:100]}...")

            # 从全局数据库加载配置
            config_mgr = load_config(GLOBAL_DB)
            config_kwargs = config_mgr.get_memory_manager_kwargs()

            # 1. 保存到项目级数据库（禁用自动总结，因为 hook 进程很快退出）
            project_db = get_project_db_path(project_name)
            project_manager = MemoryManager(db_path=project_db, **config_kwargs)
            project_msg = project_manager.add_message(session_id, "user", prompt, auto_summarize=False)
            logger.info(f"[Project] User message saved: id={project_msg.id}")

            # 2. 保存到全局数据库（禁用自动总结）
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
