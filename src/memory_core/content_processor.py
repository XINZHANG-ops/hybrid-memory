"""
内容处理模块 - 统一处理消息内容的解析、过滤和截断

用于总结生成、知识提取、历史注入等场景
"""
import json
import re
from dataclasses import dataclass
from typing import Any

@dataclass
class ContentConfig:
    """内容处理配置"""
    include_thinking: bool = False
    include_tool: bool = True
    include_text: bool = True
    max_chars_thinking: int = 200
    max_chars_tool: int = 300
    max_chars_text: int = 500


@dataclass
class ContentBlock:
    """解析后的内容块"""
    type: str  # "thinking", "tool", "text"
    content: str
    tool_name: str = ""  # 仅 tool 类型使用


def parse_content_blocks(content: str) -> list[ContentBlock]:
    """
    解析消息内容，提取 thinking/tool/text 块

    支持的格式：
    - JSON 数组: [{"type": "thinking", "content": "..."}, ...]
    - 纯文本（作为单个 text 块）
    """
    if not content or not content.strip():
        return []

    content = content.strip()

    # 尝试解析为 JSON 数组
    if content.startswith("["):
        try:
            blocks_data = json.loads(content)
            if isinstance(blocks_data, list):
                blocks = []
                for item in blocks_data:
                    if not isinstance(item, dict):
                        continue
                    block_type = item.get("type", "text")
                    block_content = item.get("content", "")
                    tool_name = item.get("name", "")

                    if block_type == "tool":
                        # tool 块可能有 name 字段
                        blocks.append(ContentBlock(
                            type="tool",
                            content=block_content,
                            tool_name=tool_name
                        ))
                    elif block_type in ("thinking", "text"):
                        blocks.append(ContentBlock(
                            type=block_type,
                            content=block_content
                        ))
                    else:
                        # 未知类型当作 text
                        blocks.append(ContentBlock(
                            type="text",
                            content=block_content
                        ))
                return blocks
        except json.JSONDecodeError:
            pass

    # 不是 JSON，作为纯文本处理（用 "plain" 类型，不加标签）
    return [ContentBlock(type="plain", content=content)]


def truncate_text(text: str, max_chars: int) -> str:
    """截断文本，保留指定字符数"""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def process_content(
    content: str,
    config: ContentConfig,
    role_label: str = ""
) -> str:
    """
    处理消息内容：解析、过滤、截断、格式化

    Args:
        content: 原始消息内容
        config: 内容处理配置
        role_label: 角色标签，如 "[User]" 或 "[Assistant]"

    Returns:
        处理后的格式化文本
    """
    blocks = parse_content_blocks(content)
    if not blocks:
        return ""

    parts = []

    for block in blocks:
        # 根据配置过滤（plain 类型跟随 text 设置）
        if block.type == "thinking" and not config.include_thinking:
            continue
        if block.type == "tool" and not config.include_tool:
            continue
        if block.type in ("text", "plain") and not config.include_text:
            continue

        # 截断和格式化
        if block.type == "thinking":
            text = truncate_text(block.content, config.max_chars_thinking)
            if text:
                parts.append(f"[Thinking] {text}")
        elif block.type == "tool":
            text = truncate_text(block.content, config.max_chars_tool)
            if text:
                tool_label = f"[Tool:{block.tool_name}]" if block.tool_name else "[Tool]"
                parts.append(f"{tool_label} {text}")
        elif block.type == "text":
            # JSON 中的 text 块，加标签
            text = truncate_text(block.content, config.max_chars_text)
            if text:
                parts.append(f"[Text] {text}")
        else:  # plain - 纯文本（用户消息），不加标签
            text = truncate_text(block.content, config.max_chars_text)
            if text:
                parts.append(text)

    if not parts:
        return ""

    # 组合结果（换行分隔）
    combined = "\n".join(parts)
    if role_label:
        return f"{role_label}\n{combined}"
    return combined


def process_messages(
    messages: list[Any],
    config: ContentConfig,
    max_total_chars: int = 0,
    user_label: str = "[User]",
    assistant_label: str = "[Assistant]"
) -> tuple[list[str], list[int]]:
    """
    批量处理消息列表

    Args:
        messages: 消息对象列表（需要有 role 和 content 属性）
        config: 内容处理配置
        max_total_chars: 总字符数限制，0 表示不限制
        user_label: 用户消息标签
        assistant_label: 助手消息标签

    Returns:
        (处理后的文本列表, 被包含的消息ID列表)
    """
    results = []
    included_ids = []
    total_chars = 0

    for msg in messages:
        role = getattr(msg, "role", "user")
        content = getattr(msg, "content", "")
        msg_id = getattr(msg, "id", None)

        label = user_label if role == "user" else assistant_label
        processed = process_content(content, config, label)

        if not processed:
            continue

        # 检查总字符限制
        if max_total_chars > 0:
            if total_chars + len(processed) > max_total_chars:
                break
            total_chars += len(processed)

        results.append(processed)
        if msg_id is not None:
            included_ids.append(msg_id)

    return results, included_ids


def config_from_dict(config_dict: dict[str, str]) -> ContentConfig:
    """从配置字典创建 ContentConfig"""
    return ContentConfig(
        include_thinking=config_dict.get("content_include_thinking", "false").lower() == "true",
        include_tool=config_dict.get("content_include_tool", "true").lower() == "true",
        include_text=config_dict.get("content_include_text", "true").lower() == "true",
        max_chars_thinking=int(config_dict.get("content_max_chars_thinking", "200")),
        max_chars_tool=int(config_dict.get("content_max_chars_tool", "300")),
        max_chars_text=int(config_dict.get("content_max_chars_text", "500")),
    )
