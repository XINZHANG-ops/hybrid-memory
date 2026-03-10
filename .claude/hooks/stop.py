#!/usr/bin/env python3
"""
Stop Hook - 双层记忆系统
同时保存助手回复到：
- 项目级数据库
- 全局数据库
"""
import sys
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime

# DEBUG: 在最开头写一个标记文件，确认 hook 是否被触发
_debug_file = Path(__file__).parent.parent.parent / "data" / "stop_hook_debug.txt"
_debug_file.parent.mkdir(parents=True, exist_ok=True)
with open(_debug_file, "a") as f:
    f.write(f"{datetime.now().isoformat()} - Stop hook started\n")

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger
from src.memory_core import MemoryManager, TokenUsage, Database, publish_event, Interaction
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


def is_real_user_message(msg: dict) -> bool:
    """判断是否为真正的用户消息（非 tool_result）"""
    if msg.get("type") != "user":
        return False
    inner_msg = msg.get("message", {})
    content = inner_msg.get("content", [])
    if isinstance(content, str):
        return True
    if isinstance(content, list) and len(content) > 0:
        # 如果第一个内容项是 tool_result，则不是真正的用户消息
        first_item = content[0]
        if isinstance(first_item, dict) and first_item.get("type") == "tool_result":
            return False
    return True


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量

    粗略估算：1 token ≈ 4 英文字符 或 1.5 中文字符
    """
    if not text:
        return 0
    # 统计中文字符数
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    # 中文约 1.5 字符/token，其他约 4 字符/token
    return int(chinese_chars / 1.5 + other_chars / 4)


def get_processed_message_ids(db_path: Path) -> set:
    """从数据库获取已处理过的 message_id 集合"""
    try:
        import sqlite3
        if not db_path.exists():
            return set()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # 检查表是否存在
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
        # 确保表存在
        conn.execute("CREATE TABLE IF NOT EXISTS processed_msg_ids (msg_id TEXT PRIMARY KEY)")
        for msg_id in msg_ids:
            conn.execute("INSERT OR IGNORE INTO processed_msg_ids (msg_id) VALUES (?)", (msg_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error saving processed message ids: {e}")


def finalize_pending_permissions(db: Database, session_id: str):
    """将所有 pending 的权限请求标记为 no（用户未批准）"""
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
    """从 transcript 文件中提取 token 使用信息（仅计算增量）

    Claude API 的 usage 字段包含：
    - input_tokens: 不包含缓存的新输入 token
    - cache_creation_input_tokens: 用于创建缓存的 token
    - cache_read_input_tokens: 从缓存读取的 token

    真实输入 = input_tokens + cache_creation_input_tokens + cache_read_input_tokens

    Output tokens 由于 Claude Code transcript 格式限制无法准确获取，
    改用文本内容长度估算（包含 thinking、text、tool_use）。

    重要：只计算未处理过的新消息，避免重复计数。
    """
    model = ""
    # 按消息 id 分组
    message_usage = {}  # {msg_id: {"input": ..., "output_text": "", "model": ...}}

    # 获取已处理过的消息 ID
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

                        # 跳过 synthetic 消息
                        if msg_model == "<synthetic>":
                            continue

                        if not msg_id:
                            continue

                        # 跳过已处理过的消息
                        if msg_id in processed_ids:
                            # 但仍然获取 model 信息
                            if not model and msg_model:
                                model = msg_model
                            continue

                        usage = inner_msg.get("usage", {})
                        content = inner_msg.get("content", [])

                        # 计算 input tokens（增量：只计算新增的，不含缓存读取）
                        if msg_id not in message_usage:
                            input_tokens = (
                                usage.get("input_tokens", 0) +
                                usage.get("cache_creation_input_tokens", 0)
                                # 不计入 cache_read_input_tokens，因为那是重复读取的
                            ) if usage else 0
                            message_usage[msg_id] = {
                                "input": input_tokens,
                                "output_text": "",
                                "model": msg_model
                            }
                            new_msg_ids.add(msg_id)

                        # 收集输出内容（覆盖而非累加，因为流式输出最后一次是完整版）
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
                        # 用当前内容覆盖（流式输出，最后一次是完整版）
                        message_usage[msg_id]["output_text"] = output_text

                        if not model and msg_model:
                            model = msg_model
                except json.JSONDecodeError:
                    continue

        # 保存新处理的消息 ID
        if new_msg_ids:
            save_processed_message_ids(project_db, new_msg_ids)

        # 汇总新消息的 token（增量）
        total_input = sum(m["input"] for m in message_usage.values())
        # Output 使用估算
        total_output = sum(estimate_tokens(m["output_text"]) for m in message_usage.values())

        logger.debug(f"Token usage from transcript: input={total_input}, output={total_output} (estimated), model={model}, new_msgs={len(message_usage)}, skipped={len(processed_ids)}")
        return {"input_tokens": total_input, "output_tokens": total_output, "model": model}
    except Exception as e:
        logger.error(f"Error extracting token usage: {e}")
        return {"input_tokens": 0, "output_tokens": 0, "model": ""}


def extract_assistant_response_from_transcript(transcript_path: str) -> str:
    """从 transcript 文件中提取最新一轮完整的 assistant 回复

    返回 JSON 格式的内容块数组，便于前端结构化渲染：
    [{"type": "thinking", "content": "..."}, {"type": "tool", "name": "Read", "content": "..."}, {"type": "text", "content": "..."}]
    """
    try:
        path = Path(transcript_path)
        if not path.exists():
            logger.warning(f"Transcript file not found: {transcript_path}")
            return ""

        # 读取 JSONL 文件（每行一个 JSON 对象）
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

        # 找到最后一个"真正的"用户消息（非 tool_result）的位置
        last_real_user_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if is_real_user_message(messages[i]):
                last_real_user_idx = i
                break

        if last_real_user_idx == -1:
            logger.warning("No real user message found in transcript")
            return ""

        logger.debug(f"Last real user message at index {last_real_user_idx}, collecting all responses after")

        # 收集结构化的内容块
        content_blocks = []
        for msg in messages[last_real_user_idx + 1:]:
            msg_type = msg.get("type", "")

            # 处理 assistant 消息
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
                                # 格式化工具调用内容
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

        # 返回 JSON 格式的结构化数据
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
    stop_reason = input_data.get("stop_reason", "")

    # 优先从 transcript 提取结构化格式（包含 thinking、tool、text 块）
    transcript_path = input_data.get("transcript_path", "")
    response = ""

    if transcript_path:
        logger.info(f"Extracting structured response from transcript: {transcript_path}")
        response = extract_assistant_response_from_transcript(transcript_path)

    # 如果 transcript 提取失败，使用 last_assistant_message（纯文本）
    if not response:
        response = input_data.get("last_assistant_message", "")
        if response:
            logger.info(f"Using last_assistant_message as fallback (plain text, {len(response)} chars)")

    logger.info(f"Project: {project_name}, Session: {session_id}")
    logger.info(f"Stop reason: {stop_reason}, Response length: {len(response)}")

    # 项目数据库路径（提前定义，用于 token 统计）
    project_db = get_project_db_path(project_name)

    # 提取 token 使用信息（传入 project_db 用于跟踪已处理的消息）
    token_usage_data = {"input_tokens": 0, "output_tokens": 0, "model": ""}
    if transcript_path:
        token_usage_data = extract_token_usage_from_transcript(transcript_path, project_db)
        logger.info(f"Token usage: input={token_usage_data['input_tokens']}, output={token_usage_data['output_tokens']}")

    try:
        # 从全局数据库加载配置
        config_mgr = load_config(GLOBAL_DB)
        config_kwargs = config_mgr.get_memory_manager_kwargs()

        # 项目级管理器
        project_manager = MemoryManager(db_path=project_db, **config_kwargs)

        # 全局管理器
        global_manager = MemoryManager(db_path=GLOBAL_DB, **config_kwargs)
        global_session_id = f"{project_name}:{session_id}"

        if response:
            # 清理可能的无效字符
            response = sanitize_text(response)
            logger.debug(f"Response preview: {response[:100]}...")

            model_name = token_usage_data.get("model", "")
            publish_event("message", f"Saving message ({len(response)} chars)", project_name)

            # 1. 保存到项目级数据库（禁用自动总结，因为 hook 进程很快退出）
            project_msg = project_manager.add_message(session_id, "assistant", response, model=model_name, auto_summarize=False)
            logger.info(f"[Project] Assistant message saved: id={project_msg.id}, model={model_name}")

            # 2. 保存到全局数据库（禁用自动总结）
            global_msg = global_manager.add_message(global_session_id, "assistant", response, model=model_name, auto_summarize=False)
            logger.info(f"[Global] Assistant message saved: id={global_msg.id}, model={model_name}")

        else:
            logger.warning("Empty response, skipping save")

        # 3. 保存 token 使用信息
        if token_usage_data["input_tokens"] > 0 or token_usage_data["output_tokens"] > 0:
            from datetime import datetime
            usage = TokenUsage(
                id=None,
                session_id=session_id,
                input_tokens=token_usage_data["input_tokens"],
                output_tokens=token_usage_data["output_tokens"],
                model=token_usage_data["model"],
                timestamp=datetime.now()
            )
            # 保存到项目数据库
            project_db_obj = Database(project_db)
            project_db_obj.add_token_usage(usage)
            # 保存到全局数据库
            usage.session_id = global_session_id
            global_db_obj = Database(GLOBAL_DB)
            global_db_obj.add_token_usage(usage)
            logger.info(f"Token usage saved: input={usage.input_tokens}, output={usage.output_tokens}")

        # 4. 将所有 pending 的权限请求标记为 no
        finalize_pending_permissions(Database(project_db), session_id)
        finalize_pending_permissions(Database(GLOBAL_DB), global_session_id)

        # 5. 检查是否需要触发后台总结（达到阈值时）
        should_summarize = project_manager.long_term.should_summarize(session_id)
        if should_summarize and stop_reason != "end_session":
            logger.info("Summary threshold reached, starting background summary process")
            publish_event("summary", f"Threshold reached, starting background summary", project_name)

            background_script = Path(__file__).parent / "background_summary.py"
            if background_script.exists():
                cmd = [sys.executable, str(background_script), project_name, session_id, str(project_db), str(GLOBAL_DB), "--no-end-session"]
                try:
                    if sys.platform == "win32":
                        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
                    else:
                        subprocess.Popen(cmd, start_new_session=True)
                    logger.info(f"Background summary process started")
                except Exception as e:
                    logger.error(f"Failed to start background summary process: {e}")

        # 处理会话结束（在后台子进程执行，避免阻塞 hook）
        if stop_reason == "end_session":
            logger.info("End session requested, starting background process")
            publish_event("session_end", f"Session ending: {project_name}", "Starting background summary & knowledge extraction")

            background_script = Path(__file__).parent / "background_summary.py"
            if background_script.exists():
                # 使用与 hook 相同的 Python 解释器
                cmd = [sys.executable, str(background_script), project_name, session_id, str(project_db), str(GLOBAL_DB)]
                try:
                    # 启动独立进程，不等待完成
                    if sys.platform == "win32":
                        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
                    else:
                        subprocess.Popen(cmd, start_new_session=True)
                    logger.info(f"Background summary process started: {' '.join(cmd)}")
                except Exception as e:
                    logger.error(f"Failed to start background process: {e}")
            else:
                logger.warning(f"Background script not found: {background_script}")

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        logger.error(traceback.format_exc())

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
