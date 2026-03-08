from loguru import logger
from .models import Message
from .llm_client import LLMClient

SUMMARY_PROMPT_WITH_CONTEXT = """你是一个对话总结助手。请基于历史上下文，总结最新对话的关键信息。

## 历史上下文（之前的总结）：
{previous_context}

## 最新对话内容：
{conversation}

## 请总结最新对话的关键信息，包括：
1. 讨论的主要话题
2. 做出的重要决定
3. 待办事项或后续行动
4. 任何需要记住的关键上下文
5. 用户的特殊提醒跟习惯

注意：结合历史上下文，但只总结最新对话的内容。请用简洁的中文总结（不超过500字）："""

SUMMARY_PROMPT = """请总结以下对话的关键信息，包括：
1. 讨论的主要话题
2. 做出的重要决定
3. 待办事项或后续行动
4. 任何需要记住的关键上下文
5. 用户的特殊提醒跟习惯

对话内容：
{conversation}

请用简洁的中文总结（不超过500字）："""


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

    def _format_conversation(self, messages: list[Message]) -> str:
        logger.debug(f"Formatting {len(messages)} messages for summary")
        lines = []
        for msg in messages:
            role_label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(
                msg.role, msg.role
            )
            lines.append(f"{role_label}: {msg.content}")
        return "\n".join(lines)
