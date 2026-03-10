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
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger
from src.memory_core import Database, Interaction
from src.memory_core.hook_utils import (
    GLOBAL_DB, setup_hook_logger, configure_utf8_stdio,
    get_project_name, get_project_db_path
)

setup_hook_logger()


def main():
    logger.info("Hook permissionRequest triggered")
    configure_utf8_stdio()

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

    tool_name = input_data.get("tool_name", "") or input_data.get("tool", "")

    # 提取请求内容
    request_content = ""
    if "tool_input" in input_data:
        tool_input = input_data["tool_input"]
        if isinstance(tool_input, dict):
            if tool_name == "Bash":
                request_content = tool_input.get("command", "")
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
        project_db = get_project_db_path(project_name)
        db = Database(project_db)

        interaction = Interaction(
            id=None,
            session_id=session_id,
            type="permission_request",
            tool_name=tool_name,
            request_content=request_content,
            options="",
            user_response="pending",
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

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
