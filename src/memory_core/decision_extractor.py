"""
Decision Extractor - 从对话中提取结构化决策

使用本地 LLM 分析对话，识别决策点并提取：
- 问题 (problem)
- 解决方案 (solution)
- 可能的原因候选 (reason_options)
- 相关文件 (files)
"""
import json
import re
from datetime import datetime
from loguru import logger
from .models import Decision
from .llm_client import LLMClient
from .content_processor import ContentConfig, process_content
from .prompts import DECISION_PROMPT


class DecisionExtractor:
    def __init__(
        self,
        llm_client: LLMClient,
        content_config: ContentConfig | None = None,
        decision_prompt: str = "",
    ):
        self.llm_client = llm_client
        self.content_config = content_config or ContentConfig()
        self.decision_prompt = decision_prompt
        logger.info(f"DecisionExtractor initialized (content_config={self.content_config})")

    def extract_decisions(
        self,
        messages: list,
        project: str,
        session_id: str,
        max_messages: int = 20,
        message_ids: list[int] | None = None
    ) -> list[Decision]:
        """
        从消息中提取所有决策（可能 0 个、1 个或多个）

        Args:
            messages: 消息列表 (包含 role 和 content)
            project: 项目名称
            session_id: 会话 ID
            max_messages: 分析的最大消息数
            message_ids: 消息 ID 列表（用于记录消息范围）

        Returns:
            Decision 对象列表（可能为空）
        """
        if not messages:
            return []

        # 只取最近的消息
        recent_messages = messages[-max_messages:]
        # 对应的消息 ID
        recent_ids = message_ids[-max_messages:] if message_ids else None

        # 格式化对话
        conversation = self._format_conversation(recent_messages)
        if not conversation.strip():
            return []

        # 使用自定义 prompt 或默认
        prompt_template = self.decision_prompt if self.decision_prompt and self.decision_prompt.strip() else DECISION_PROMPT
        try:
            prompt = prompt_template.format(conversation=conversation)
        except KeyError as e:
            logger.warning(f"Custom decision prompt format error: {e}, falling back to default")
            prompt = DECISION_PROMPT.format(conversation=conversation)

        try:
            logger.debug(f"Extracting decisions from {len(recent_messages)} messages")
            response = self.llm_client.generate(prompt)

            # 解析 JSON 响应
            result = self._parse_response(response)
            if not result:
                logger.debug("Failed to parse decision response")
                return []

            decisions_data = result.get("decisions", [])
            if not decisions_data:
                logger.debug("No decisions detected in conversation")
                return []

            # 创建 Decision 对象列表
            decisions = []
            now = datetime.now()
            msg_start = min(recent_ids) if recent_ids else None
            msg_end = max(recent_ids) if recent_ids else None
            msg_count = len(recent_ids) if recent_ids else 0
            for item in decisions_data:
                if not item.get("problem") or not item.get("solution"):
                    continue
                decision = Decision(
                    id=None,
                    project=project,
                    session_id=session_id,
                    problem=item.get("problem", ""),
                    solution=item.get("solution", ""),
                    status="pending",
                    reason="",
                    reason_options=json.dumps(item.get("reason_options", []), ensure_ascii=False),
                    note="",
                    files=json.dumps(item.get("files", []), ensure_ascii=False),
                    tags="[]",
                    message_range_start=msg_start,
                    message_range_end=msg_end,
                    message_count=msg_count,
                    timestamp=now,
                )
                decisions.append(decision)

            logger.info(f"Extracted {len(decisions)} decisions")
            return decisions

        except Exception as e:
            logger.error(f"Error extracting decisions: {e}")
            return []

    def _format_conversation(self, messages: list) -> str:
        """格式化消息为对话文本（使用 ContentConfig）"""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # 使用统一的内容处理
            processed = process_content(content, self.content_config)
            if not processed:
                continue

            role_label = "User" if role == "user" else "Assistant"
            lines.append(f"{role_label}: {processed}")

        return "\n\n".join(lines)

    def _parse_response(self, response: str) -> dict | None:
        """解析 LLM 响应中的 JSON"""
        # 尝试直接解析
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # 尝试从 markdown 代码块中提取
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试找到包含 decisions 数组的 JSON 对象
        json_match = re.search(r'\{[^{}]*"decisions"\s*:\s*\[.*?\]\s*\}', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        logger.warning(f"Failed to parse decision response: {response[:200]}...")
        return None
