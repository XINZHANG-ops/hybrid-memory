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
logger.add(LOG_FILE, level="DEBUG", rotation="1 MB")


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


def extract_assistant_response_from_transcript(transcript_path: str) -> str:
    """从 transcript 文件中提取最新一轮完整的 assistant 回复（包括所有工具调用）"""
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

        # 收集最后一个真正用户消息之后的所有 assistant 内容
        response_parts = []
        for msg in messages[last_real_user_idx + 1:]:
            msg_type = msg.get("type", "")

            # 处理 assistant 消息
            if msg_type == "assistant" and "message" in msg:
                inner_msg = msg.get("message", {})
                content = inner_msg.get("content", [])
                logger.debug(f"Found assistant message with {len(content) if isinstance(content, list) else 1} content items")

                if isinstance(content, str):
                    response_parts.append(content)
                elif isinstance(content, list):
                    for part in content:
                        part_type = part.get("type", "") if isinstance(part, dict) else "string"
                        logger.debug(f"Processing content part: type={part_type}")

                        if isinstance(part, str):
                            response_parts.append(part)
                        elif isinstance(part, dict):
                            if part_type == "text":
                                text = part.get("text", "")
                                if text:
                                    response_parts.append(text)
                            elif part_type == "tool_use":
                                tool_name = part.get("name", "unknown")
                                tool_input = part.get("input", {})
                                # 对于代码编辑工具，提取关键信息
                                if tool_name in ("Edit", "Write"):
                                    file_path = tool_input.get("file_path", "")
                                    if tool_name == "Edit":
                                        old_str = tool_input.get("old_string", "")[:100]
                                        new_str = tool_input.get("new_string", "")[:300]
                                        response_parts.append(f"[Tool: {tool_name}] {file_path}\n  旧: {old_str}...\n  新: {new_str}...")
                                    else:
                                        content_preview = tool_input.get("content", "")[:300]
                                        response_parts.append(f"[Tool: {tool_name}] {file_path}\n  内容: {content_preview}...")
                                elif tool_name == "Bash":
                                    cmd = tool_input.get("command", "")[:200]
                                    response_parts.append(f"[Tool: Bash] {cmd}")
                                elif tool_name == "Read":
                                    file_path = tool_input.get("file_path", "")
                                    response_parts.append(f"[Tool: Read] {file_path}")
                                else:
                                    input_str = json.dumps(tool_input, ensure_ascii=False)[:300]
                                    response_parts.append(f"[Tool: {tool_name}] {input_str}")
                            elif part_type == "thinking":
                                # 简短记录思考过程
                                thinking = part.get("thinking", "")[:150]
                                if thinking:
                                    response_parts.append(f"[思考] {thinking}...")

        result = "\n".join(response_parts)
        logger.info(f"Extracted assistant response: {len(result)} chars from {len(response_parts)} parts")
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

    # 优先使用 last_assistant_message（Claude Code 直接提供）
    response = input_data.get("last_assistant_message", "")

    # 如果没有，尝试从 transcript 提取
    if not response:
        transcript_path = input_data.get("transcript_path", "")
        if transcript_path:
            logger.info(f"No last_assistant_message, extracting from transcript: {transcript_path}")
            response = extract_assistant_response_from_transcript(transcript_path)

    logger.info(f"Project: {project_name}, Session: {session_id}")
    logger.info(f"Stop reason: {stop_reason}, Response length: {len(response)}")

    try:
        # 从全局数据库加载配置
        config_mgr = load_config(GLOBAL_DB)
        config_kwargs = config_mgr.get_memory_manager_kwargs()

        # 项目级管理器
        project_db = get_project_db_path(project_name)
        project_manager = MemoryManager(db_path=project_db, **config_kwargs)

        # 全局管理器
        global_manager = MemoryManager(db_path=GLOBAL_DB, **config_kwargs)
        global_session_id = f"{project_name}:{session_id}"

        if response:
            # 清理可能的无效字符
            response = sanitize_text(response)
            logger.debug(f"Response preview: {response[:100]}...")

            # 1. 保存到项目级数据库
            project_msg = project_manager.add_message(session_id, "assistant", response)
            logger.info(f"[Project] Assistant message saved: id={project_msg.id}")

            # 2. 保存到全局数据库
            global_msg = global_manager.add_message(global_session_id, "assistant", response)
            logger.info(f"[Global] Assistant message saved: id={global_msg.id}")

        else:
            logger.warning("Empty response, skipping save")

        # 处理会话结束
        if stop_reason == "end_session":
            logger.info("End session requested")

            # 提取结构化知识（在总结之前）
            try:
                knowledge = project_manager.extract_knowledge(session_id)
                if knowledge:
                    total_items = sum(len(v) for v in knowledge.values())
                    logger.info(f"[Project] Extracted knowledge: {total_items} items")
            except Exception as e:
                logger.warning(f"Knowledge extraction failed: {e}")

            # 项目级会话结束
            project_summary = project_manager.end_session(session_id)
            if project_summary:
                logger.info(f"[Project] Session ended with summary: id={project_summary.id}")

            # 全局会话结束
            global_summary = global_manager.end_session(global_session_id)
            if global_summary:
                logger.info(f"[Global] Session ended with summary: id={global_summary.id}")

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        logger.error(traceback.format_exc())

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
