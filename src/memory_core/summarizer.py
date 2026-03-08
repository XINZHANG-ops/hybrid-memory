from loguru import logger
from .models import Message
from .llm_client import LLMClient

SUMMARY_PROMPT_WITH_CONTEXT = """# 任务：总结对话

你是一个总结助手，不是对话参与者。不要继续对话，只需要输出总结。

## 历史背景
{previous_context}

## 需要总结的对话
{conversation}

## 输出要求
直接输出总结文本（不要输出"助手:"或任何角色标签），包括：
1. 主要话题
2. 重要决定
3. 待办事项
4. 关键上下文

用简洁中文，不超过500字。"""

SUMMARY_PROMPT = """# 任务：总结对话

你是一个总结助手，不是对话参与者。不要继续对话，只需要输出总结。

## 需要总结的对话
{conversation}

## 输出要求
直接输出总结文本（不要输出"助手:"或任何角色标签），包括：
1. 主要话题
2. 重要决定
3. 待办事项
4. 关键上下文

用简洁中文，不超过500字。"""


class SummaryGenerator:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        logger.info("SummaryGenerator initialized")

    def generate(self, messages: list[Message], previous_context: str = "") -> str:
        if not messages:
            logger.debug("No messages to summarize")
            return ""
        logger.info(f"Generating summary for {len(messages)} messages (with context: {len(previous_context)} chars)")
        conversation = self._format_conversation(messages)
        logger.debug(f"Formatted conversation length: {len(conversation)} chars")

        if previous_context:
            prompt = SUMMARY_PROMPT_WITH_CONTEXT.format(
                previous_context=previous_context,
                conversation=conversation
            )
            logger.debug("Using prompt with historical context")
        else:
            prompt = SUMMARY_PROMPT.format(conversation=conversation)
            logger.debug("Using basic prompt (no historical context)")

        logger.debug(f"Summary prompt length: {len(prompt)} chars")
        logger.debug("Calling LLM for summary generation...")
        result = self.llm.generate(prompt)
        logger.info(f"Summary generated: {len(result)} chars")
        logger.debug(f"Summary preview: {result[:200]}...")
        return result

    def _format_conversation(self, messages: list[Message], max_chars: int = 8000) -> str:
        """格式化对话内容，限制总长度避免 prompt 过长"""
        logger.debug(f"Formatting {len(messages)} messages for summary (max_chars={max_chars})")
        lines = []
        total_chars = 0
        # 优先保留最近的消息
        for msg in reversed(messages):
            role_label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(
                msg.role, msg.role
            )
            # 每条消息最多500字符
            content = msg.content[:500] if len(msg.content) > 500 else msg.content
            line = f"{role_label}: {content}"
            if total_chars + len(line) > max_chars:
                break
            lines.insert(0, line)
            total_chars += len(line)
        logger.debug(f"Formatted {len(lines)} messages, total {total_chars} chars")
        return "\n".join(lines)
