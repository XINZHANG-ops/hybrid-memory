#!/usr/bin/env python3
"""
PermissionRequest Hook - 记录权限请求交互

当 Claude 请求用户确认（yes/no）时触发，记录：
- 请求的工具名称
- 请求内容
- 用户的响应（在后续 hook 中更新）
"""
import sys
import json
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger
from src.memory_core import Database, Interaction

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


def main():
    logger.info("Hook permissionRequest triggered")

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

    logger.debug(f"PermissionRequest input data: {json.dumps(input_data, ensure_ascii=False, default=str)[:500]}")

    project_name = get_project_name()
    session_id = input_data.get("session_id") or f"{project_name}-session"

    # 提取权限请求信息
    tool_name = input_data.get("tool_name", "") or input_data.get("tool", "")

    # 尝试从不同字段获取请求内容
    request_content = ""
    if "tool_input" in input_data:
        tool_input = input_data["tool_input"]
        if isinstance(tool_input, dict):
            # 对于 Bash 工具，提取 command
            if tool_name == "Bash":
                request_content = tool_input.get("command", "")
            # 对于 Edit/Write 工具，提取 file_path
            elif tool_name in ("Edit", "Write"):
                request_content = tool_input.get("file_path", "")
            else:
                request_content = json.dumps(tool_input, ensure_ascii=False)
        else:
            request_content = str(tool_input)
    elif "command" in input_data:
        request_content = input_data["command"]
    elif "description" in input_data:
        request_content = input_data["description"]

    logger.info(f"Permission request: tool={tool_name}, content={request_content[:100]}...")

    try:
        # 记录到项目数据库
        project_db = get_project_db_path(project_name)
        db = Database(project_db)

        interaction = Interaction(
            id=None,
            session_id=session_id,
            type="permission_request",
            tool_name=tool_name,
            request_content=request_content,
            options="",
            user_response="pending",  # 将在用户响应后更新
            timestamp=datetime.now()
        )

        saved = db.add_interaction(interaction)
        logger.info(f"Permission request recorded: id={saved.id}, tool={tool_name}")

        # 同时记录到全局数据库
        global_db = Database(GLOBAL_DB)
        interaction.session_id = f"{project_name}:{session_id}"
        interaction.id = None
        global_db.add_interaction(interaction)

    except Exception as e:
        logger.error(f"Error recording permission request: {e}")
        import traceback
        logger.error(traceback.format_exc())

    # 不干预用户决定，继续正常流程
    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
