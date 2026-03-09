from loguru import logger
from .models import Message
from .llm_client import LLMClient
from .prompts import SUMMARY_PROMPT, SUMMARY_PROMPT_WITH_CONTEXT, ROLE_LABELS

# 默认值（可通过配置覆盖）
DEFAULT_MAX_CHARS_TOTAL = 8000
DEFAULT_MAX_CHARS_PER_MESSAGE = 500


class SummaryGenerator:
    def __init__(
        self,
        llm_client: LLMClient,
        max_chars_total: int = DEFAULT_MAX_CHARS_TOTAL,
        max_chars_per_message: int = DEFAULT_MAX_CHARS_PER_MESSAGE,
    ):
        self.llm = llm_client
        self.max_chars_total = max_chars_total
        self.max_chars_per_message = max_chars_per_message
        logger.info(f"SummaryGenerator initialized (max_total={max_chars_total}, max_per_msg={max_chars_per_message})")

    def generate(self, messages: list[Message], previous_context: str = "", custom_template: str = "") -> str:
        if not messages:
            logger.debug("No messages to summarize")
            return ""
        logger.info(f"Generating summary for {len(messages)} messages (with context: {len(previous_context)} chars)")
        conversation = self._format_conversation(messages, self.max_chars_total, self.max_chars_per_message)
        logger.debug(f"Formatted conversation length: {len(conversation)} chars")

        if custom_template and custom_template.strip():
            try:
                prompt = custom_template.format(
                    previous_context=previous_context,
                    conversation=conversation
                )
                logger.debug("Using custom prompt template")
            except KeyError as e:
                logger.warning(f"Custom template format error: {e}, falling back to default")
                custom_template = ""

        if not custom_template or not custom_template.strip():
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

    def _format_conversation(
        self,
        messages: list[Message],
        max_chars_total: int = DEFAULT_MAX_CHARS_TOTAL,
        max_chars_per_message: int = DEFAULT_MAX_CHARS_PER_MESSAGE,
    ) -> str:
        """格式化对话内容，限制总长度避免 prompt 过长"""
        logger.debug(f"Formatting {len(messages)} messages for summary (max_total={max_chars_total}, max_per_msg={max_chars_per_message})")
        lines = []
        total_chars = 0
        # 优先保留最近的消息
        for msg in reversed(messages):
            role_label = ROLE_LABELS.get(msg.role, msg.role)
            # 每条消息最多 max_chars_per_message 字符
            content = msg.content[:max_chars_per_message] if len(msg.content) > max_chars_per_message else msg.content
            line = f"{role_label}: {content}"
            if total_chars + len(line) > max_chars_total:
                break
            lines.insert(0, line)
            total_chars += len(line)
        logger.debug(f"Formatted {len(lines)} messages, total {total_chars} chars")
        return "\n".join(lines)
