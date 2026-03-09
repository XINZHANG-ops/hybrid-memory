from loguru import logger
from .models import Message, Interaction
from .llm_client import LLMClient
from .prompts import SUMMARY_PROMPT, SUMMARY_PROMPT_WITH_CONTEXT, ROLE_LABELS
from .content_processor import ContentConfig, process_content

# 默认值（可通过配置覆盖）
DEFAULT_MAX_CHARS_TOTAL = 8000


class SummaryGenerator:
    def __init__(
        self,
        llm_client: LLMClient,
        max_chars_total: int = DEFAULT_MAX_CHARS_TOTAL,
        content_config: ContentConfig | None = None,
    ):
        self.llm = llm_client
        self.max_chars_total = max_chars_total
        self.content_config = content_config or ContentConfig()
        logger.info(f"SummaryGenerator initialized (max_total={max_chars_total}, content_config={self.content_config})")

    def generate(self, messages: list[Message], previous_context: str = "", custom_template: str = "", interactions: list[Interaction] | None = None) -> str:
        if not messages:
            logger.debug("No messages to summarize")
            return ""
        logger.info(f"Generating summary for {len(messages)} messages (with context: {len(previous_context)} chars, interactions: {len(interactions) if interactions else 0})")
        conversation = self._format_conversation(messages, self.max_chars_total, interactions)
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
        interactions: list[Interaction] | None = None,
    ) -> str:
        """格式化对话内容，使用 content_processor 解析和截断"""
        logger.debug(f"Formatting {len(messages)} messages for summary (max_total={max_chars_total})")
        lines = []
        total_chars = 0

        # 建立消息时间戳索引，用于关联 interactions
        msg_timestamps = [(msg.timestamp, msg) for msg in messages]

        # 优先保留最近的消息
        for i, msg in enumerate(reversed(messages)):
            role_label = ROLE_LABELS.get(msg.role, msg.role)
            # 使用 content_processor 处理内容
            content = process_content(msg.content, self.content_config)
            if not content:
                continue  # 用户关闭了所有类型，跳过此消息

            # 为助手消息附加关联的 interactions
            interaction_text = ""
            if interactions and msg.role == "assistant":
                # 找到在当前消息时间戳之前、上一条消息之后的 interactions
                prev_timestamp = msg_timestamps[len(messages) - i - 2][0] if i < len(messages) - 1 else None
                related = self._get_interactions_for_message(interactions, msg.timestamp, prev_timestamp)
                if related:
                    int_lines = []
                    for intr in related:
                        response_label = "approved" if intr.user_response == "yes" else ("rejected" if intr.user_response == "no" else intr.user_response)
                        int_lines.append(f"  [{intr.type}] {intr.tool_name}: {response_label}")
                    interaction_text = "\n" + "\n".join(int_lines)

            line = f"{role_label}: {content}{interaction_text}"
            if total_chars + len(line) > max_chars_total:
                break
            lines.insert(0, line)
            total_chars += len(line)
        logger.debug(f"Formatted {len(lines)} messages, total {total_chars} chars")
        return "\n".join(lines)

    def _get_interactions_for_message(self, interactions: list[Interaction], msg_timestamp, prev_timestamp) -> list[Interaction]:
        """获取在消息时间戳之前、上一条消息之后的 interactions"""
        result = []
        for intr in interactions:
            if intr.timestamp <= msg_timestamp:
                if prev_timestamp is None or intr.timestamp > prev_timestamp:
                    result.append(intr)
        return result
