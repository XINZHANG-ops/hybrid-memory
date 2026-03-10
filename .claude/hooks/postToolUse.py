#!/usr/bin/env python3
"""
PostToolUse Hook - 记录工具执行后的交互

功能：
1. 更新之前 pending 的 PermissionRequest 为 "yes"（工具执行成功意味着用户批准）
2. 检测 AskUserQuestion 工具调用，记录用户的选择
"""
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger
from src.memory_core import Database, Interaction
from src.memory_core.hook_utils import (
    GLOBAL_DB, setup_hook_logger, configure_utf8_stdio,
    get_project_name, get_project_db_path
)

setup_hook_logger()


def update_pending_permission(db: Database, session_id: str, tool_name: str):
    """更新最近的 pending permission request 为 yes"""
    try:
        now = datetime.now()
        start_time = now - timedelta(minutes=5)

        with db._connect() as conn:
            row = conn.execute(
                """SELECT id FROM interactions
                   WHERE session_id = ? AND type = 'permission_request'
                   AND user_response = 'pending' AND tool_name = ?
                   AND timestamp >= ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (session_id, tool_name, start_time)
            ).fetchone()

            if row:
                conn.execute(
                    "UPDATE interactions SET user_response = 'yes' WHERE id = ?",
                    (row[0],)
                )
                logger.info(f"Updated permission request #{row[0]} to 'yes'")
                return True
    except Exception as e:
        logger.error(f"Error updating pending permission: {e}")
    return False


def main():
    logger.info("Hook postToolUse triggered")
    configure_utf8_stdio()

    try:
        raw_input = sys.stdin.read()
        logger.debug(f"Raw input length: {len(raw_input)}")
        input_data = json.loads(raw_input) if raw_input.strip() else {}
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        input_data = {}

    logger.debug(f"PostToolUse input data: {json.dumps(input_data, ensure_ascii=False, default=str)[:1000]}")

    project_name = get_project_name()
    session_id = input_data.get("session_id") or f"{project_name}-session"

    tool_name = input_data.get("tool_name", "") or input_data.get("tool", "")
    tool_input = input_data.get("tool_input", {})
    tool_response = input_data.get("tool_response", {})

    logger.info(f"PostToolUse: tool={tool_name}")

    try:
        project_db = get_project_db_path(project_name)
        db = Database(project_db)

        # 1. 更新之前 pending 的 permission request
        update_pending_permission(db, session_id, tool_name)

        # 2. 检测 AskUserQuestion 工具，记录用户选择
        if tool_name == "AskUserQuestion":
            logger.info("AskUserQuestion detected, recording user choice")

            questions = tool_input.get("questions", []) if isinstance(tool_input, dict) else []
            options_str = json.dumps(questions, ensure_ascii=False) if questions else ""

            user_response = ""
            if isinstance(tool_response, dict) and "answers" in tool_response:
                answers = tool_response["answers"]
                if isinstance(answers, dict):
                    choices = list(answers.values())
                    user_response = choices[0] if len(choices) == 1 else json.dumps(choices, ensure_ascii=False)
                else:
                    user_response = str(answers)
            elif isinstance(tool_response, str):
                user_response = tool_response

            interaction = Interaction(
                id=None,
                session_id=session_id,
                type="user_choice",
                tool_name="AskUserQuestion",
                request_content=questions[0].get("question", "") if questions else "",
                options=options_str,
                user_response=user_response,
                timestamp=datetime.now()
            )

            saved = db.add_interaction(interaction)
            logger.info(f"User choice recorded: id={saved.id}")

            # 同时记录到全局数据库
            global_db = Database(GLOBAL_DB)
            interaction.session_id = f"{project_name}:{session_id}"
            interaction.id = None
            global_db.add_interaction(interaction)

    except Exception as e:
        logger.error(f"Error in postToolUse hook: {e}")
        import traceback
        logger.error(traceback.format_exc())

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
