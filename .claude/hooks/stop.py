#!/usr/bin/env python3
"""
Stop Hook - 双层记忆系统
同时保存助手回复到：
- 项目级数据库
- 全局数据库
"""
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger
from src.memory_core import MemoryManager, TokenUsage, Database, publish_event
from src.memory_core.config import load_config
from src.memory_core.hook_utils import (
    GLOBAL_DB, setup_hook_logger, configure_utf8_stdio,
    get_project_name, get_project_db_path, sanitize_text
)

setup_hook_logger()


def is_real_user_message(msg: dict) -> bool:
    """判断是否为真正的用户消息（非 tool_result）"""
    if msg.get("type") != "user":
        return False
    inner_msg = msg.get("message", {})
    content = inner_msg.get("content", [])
    if isinstance(content, str):
        return True
    if isinstance(content, list) and len(content) > 0:
        first_item = content[0]
        if isinstance(first_item, dict) and first_item.get("type") == "tool_result":
            return False
    return True


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量（1 token ≈ 4 英文字符 或 1.5 中文字符）"""
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


def get_processed_message_ids(db_path: Path) -> set:
    """从数据库获取已处理过的 message_id 集合（用于 token 计算去重）"""
    try:
        import sqlite3
        if not db_path.exists():
            return set()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_msg_ids'"
        )
        if not cursor.fetchone():
            conn.execute("CREATE TABLE processed_msg_ids (msg_id TEXT PRIMARY KEY)")
            conn.commit()
            conn.close()
            return set()
        rows = conn.execute("SELECT msg_id FROM processed_msg_ids").fetchall()
        conn.close()
        return {row["msg_id"] for row in rows}
    except Exception as e:
        logger.error(f"Error getting processed message ids: {e}")
        return set()


def save_processed_message_ids(db_path: Path, msg_ids: set):
    """保存已处理的 message_id 到数据库"""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS processed_msg_ids (msg_id TEXT PRIMARY KEY)")
        conn.executemany(
            "INSERT OR IGNORE INTO processed_msg_ids (msg_id) VALUES (?)",
            [(msg_id,) for msg_id in msg_ids]
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error saving processed message ids: {e}")


def finalize_pending_permissions(db: Database, session_id: str):
    """将所有 pending 的权限请求标记为 no"""
    try:
        with db._connect() as conn:
            result = conn.execute(
                """UPDATE interactions SET user_response = 'no'
                   WHERE session_id = ? AND type = 'permission_request' AND user_response = 'pending'""",
                (session_id,)
            )
            if result.rowcount > 0:
                logger.info(f"Marked {result.rowcount} pending permission requests as 'no'")
    except Exception as e:
        logger.error(f"Error finalizing pending permissions: {e}")


def extract_token_usage_from_transcript(transcript_path: str, project_db: Path) -> dict:
    """从 transcript 文件中提取 token 使用信息（仅计算增量）"""
    model = ""
    message_usage = {}
    processed_ids = get_processed_message_ids(project_db)
    new_msg_ids = set()

    try:
        path = Path(transcript_path)
        if not path.exists():
            return {"input_tokens": 0, "output_tokens": 0, "model": ""}

        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if msg.get("type") == "assistant" and "message" in msg:
                        inner_msg = msg.get("message", {})
                        msg_id = inner_msg.get("id", "")
                        msg_model = inner_msg.get("model", "")

                        if msg_model == "<synthetic>" or not msg_id:
                            continue

                        if msg_id in processed_ids:
                            if not model and msg_model:
                                model = msg_model
                            continue

                        usage = inner_msg.get("usage", {})
                        content = inner_msg.get("content", [])

                        if msg_id not in message_usage:
                            input_tokens = (
                                usage.get("input_tokens", 0) +
                                usage.get("cache_creation_input_tokens", 0)
                            ) if usage else 0
                            message_usage[msg_id] = {
                                "input": input_tokens,
                                "output_text": "",
                                "model": msg_model
                            }
                            new_msg_ids.add(msg_id)

                        output_text = ""
                        if isinstance(content, list):
                            for part in content:
                                if isinstance(part, dict):
                                    part_type = part.get("type", "")
                                    if part_type == "thinking":
                                        output_text += part.get("thinking", "")
                                    elif part_type == "text":
                                        output_text += part.get("text", "")
                                    elif part_type == "tool_use":
                                        tool_name = part.get("name", "")
                                        tool_input = json.dumps(part.get("input", {}), ensure_ascii=False)
                                        output_text += f"{tool_name}: {tool_input}"
                        message_usage[msg_id]["output_text"] = output_text

                        if not model and msg_model:
                            model = msg_model
                except json.JSONDecodeError:
                    continue

        if new_msg_ids:
            save_processed_message_ids(project_db, new_msg_ids)

        total_input = sum(m["input"] for m in message_usage.values())
        total_output = sum(estimate_tokens(m["output_text"]) for m in message_usage.values())

        logger.debug(f"Token usage from transcript: input={total_input}, output={total_output} (estimated), model={model}, new_msgs={len(message_usage)}, skipped={len(processed_ids)}")
        return {"input_tokens": total_input, "output_tokens": total_output, "model": model}
    except Exception as e:
        logger.error(f"Error extracting token usage: {e}")
        return {"input_tokens": 0, "output_tokens": 0, "model": ""}


def extract_assistant_response_from_transcript(transcript_path: str) -> str:
    """从 transcript 文件中提取最新一轮完整的 assistant 回复"""
    try:
        path = Path(transcript_path)
        if not path.exists():
            logger.warning(f"Transcript file not found: {transcript_path}")
            return ""

        messages = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        logger.debug(f"Loaded {len(messages)} messages from transcript")

        last_real_user_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if is_real_user_message(messages[i]):
                last_real_user_idx = i
                break

        if last_real_user_idx == -1:
            logger.warning("No real user message found in transcript")
            return ""

        logger.debug(f"Last real user message at index {last_real_user_idx}, collecting all responses after")

        content_blocks = []
        for msg in messages[last_real_user_idx + 1:]:
            msg_type = msg.get("type", "")

            if msg_type == "assistant" and "message" in msg:
                inner_msg = msg.get("message", {})
                content = inner_msg.get("content", [])
                logger.debug(f"Found assistant message with {len(content) if isinstance(content, list) else 1} content items")

                if isinstance(content, str):
                    content_blocks.append({"type": "text", "content": content})
                elif isinstance(content, list):
                    for part in content:
                        part_type = part.get("type", "") if isinstance(part, dict) else "string"
                        logger.debug(f"Processing content part: type={part_type}")

                        if isinstance(part, str):
                            content_blocks.append({"type": "text", "content": part})
                        elif isinstance(part, dict):
                            if part_type == "text":
                                text = part.get("text", "")
                                if text:
                                    content_blocks.append({"type": "text", "content": text})
                            elif part_type == "tool_use":
                                tool_name = part.get("name", "unknown")
                                tool_input = part.get("input", {})
                                if tool_name in ("Edit", "Write"):
                                    file_path = tool_input.get("file_path", "")
                                    if tool_name == "Edit":
                                        old_str = tool_input.get("old_string", "")
                                        new_str = tool_input.get("new_string", "")
                                        tool_content = f"{file_path}\nold: {old_str}\nnew: {new_str}"
                                    else:
                                        content_full = tool_input.get("content", "")
                                        tool_content = f"{file_path}\ncontent: {content_full}"
                                elif tool_name == "Bash":
                                    tool_content = tool_input.get("command", "")
                                elif tool_name == "Read":
                                    tool_content = tool_input.get("file_path", "")
                                else:
                                    tool_content = json.dumps(tool_input, ensure_ascii=False)
                                content_blocks.append({"type": "tool", "name": tool_name, "content": tool_content})
                            elif part_type == "thinking":
                                thinking = part.get("thinking", "")
                                if thinking:
                                    content_blocks.append({"type": "thinking", "content": thinking})

        result = json.dumps(content_blocks, ensure_ascii=False)
        logger.info(f"Extracted {len(content_blocks)} content blocks")
        return result
    except Exception as e:
        logger.error(f"Error extracting from transcript: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return ""


def main():
    logger.info("Hook stop triggered")
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
    stop_reason = input_data.get("stop_reason", "")

    transcript_path = input_data.get("transcript_path", "")
    response = ""

    if transcript_path:
        logger.info(f"Extracting structured response from transcript: {transcript_path}")
        response = extract_assistant_response_from_transcript(transcript_path)

    if not response:
        response = input_data.get("last_assistant_message", "")
        if response:
            logger.info(f"Using last_assistant_message as fallback (plain text, {len(response)} chars)")

    logger.info(f"Project: {project_name}, Session: {session_id}")
    logger.info(f"Stop reason: {stop_reason}, Response length: {len(response)}")

    project_db = get_project_db_path(project_name)

    token_usage_data = {"input_tokens": 0, "output_tokens": 0, "model": ""}
    if transcript_path:
        token_usage_data = extract_token_usage_from_transcript(transcript_path, project_db)
        logger.info(f"Token usage: input={token_usage_data['input_tokens']}, output={token_usage_data['output_tokens']}")

    try:
        config_mgr = load_config(GLOBAL_DB)
        config_kwargs = config_mgr.get_memory_manager_kwargs()

        project_manager = MemoryManager(db_path=project_db, **config_kwargs)
        global_manager = MemoryManager(db_path=GLOBAL_DB, **config_kwargs)
        global_session_id = f"{project_name}:{session_id}"

        if response:
            response = sanitize_text(response)
            logger.debug(f"Response preview: {response[:100]}...")

            model_name = token_usage_data.get("model", "")
            publish_event("message", f"Saving message ({len(response)} chars)", project_name)

            project_msg = project_manager.add_message(session_id, "assistant", response, model=model_name, auto_summarize=False)
            logger.info(f"[Project] Assistant message saved: id={project_msg.id}, model={model_name}")

            global_msg = global_manager.add_message(global_session_id, "assistant", response, model=model_name, auto_summarize=False)
            logger.info(f"[Global] Assistant message saved: id={global_msg.id}, model={model_name}")

        else:
            logger.warning("Empty response, skipping save")

        # 保存 token 使用信息
        if token_usage_data["input_tokens"] > 0 or token_usage_data["output_tokens"] > 0:
            usage = TokenUsage(
                id=None,
                session_id=session_id,
                input_tokens=token_usage_data["input_tokens"],
                output_tokens=token_usage_data["output_tokens"],
                model=token_usage_data["model"],
                timestamp=datetime.now()
            )
            project_db_obj = Database(project_db)
            project_db_obj.add_token_usage(usage)
            usage.session_id = global_session_id
            global_db_obj = Database(GLOBAL_DB)
            global_db_obj.add_token_usage(usage)
            logger.info(f"Token usage saved: input={usage.input_tokens}, output={usage.output_tokens}")

        # 将所有 pending 的权限请求标记为 no
        finalize_pending_permissions(Database(project_db), session_id)
        finalize_pending_permissions(Database(GLOBAL_DB), global_session_id)

        # 启动后台进程
        background_script = Path(__file__).parent / "background_summary.py"
        if background_script.exists():
            should_summarize = project_manager.long_term.should_summarize(session_id)
            is_end_session = stop_reason == "end_session"

            cmd = [sys.executable, str(background_script), project_name, session_id, str(project_db), str(GLOBAL_DB)]
            if not is_end_session:
                cmd.append("--no-end-session")
            if not should_summarize and not is_end_session:
                cmd.append("--embedding-only")

            if is_end_session:
                logger.info("End session requested, starting background process")
                publish_event("session_end", f"Session ending", project_name)
            elif should_summarize:
                logger.info("Summary threshold reached, starting background process")
                publish_event("summary", f"Starting background summary", project_name)
            else:
                logger.debug("Starting background process for embedding")

            try:
                if sys.platform == "win32":
                    subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    subprocess.Popen(cmd, start_new_session=True)
                logger.info(f"Background process started")
            except Exception as e:
                logger.error(f"Failed to start background process: {e}")

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        logger.error(traceback.format_exc())

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
