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


DECISION_EXTRACTION_PROMPT = """Analyze the following conversation and identify if there's a **decision point** - a moment where a problem was identified and a solution was chosen.

A decision point includes:
1. A problem or issue that was encountered
2. A solution or approach that was taken
3. The reasoning behind the choice

If you find a decision point, extract it in the following JSON format:
```json
{{
  "has_decision": true,
  "problem": "Brief description of the problem (1-2 sentences)",
  "solution": "Brief description of the solution taken (1-2 sentences)",
  "reason_options": [
    "Possible reason 1",
    "Possible reason 2",
    "Possible reason 3"
  ],
  "files": ["file1.py", "file2.js"]
}}
```

If there's no clear decision point, return:
```json
{{
  "has_decision": false
}}
```

Guidelines:
- Focus on technical decisions, not routine actions
- A decision involves choosing between alternatives or solving a non-trivial problem
- reason_options should be 2-4 plausible reasons (the user will select the correct one)
- files should list only the main files involved (1-3 files max)
- Keep problem and solution concise

Conversation:
{conversation}

Extract the decision (JSON only):"""


class DecisionExtractor:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        logger.info("DecisionExtractor initialized")

    def extract_decision(
        self,
        messages: list,
        project: str,
        session_id: str,
        max_messages: int = 10
    ) -> Decision | None:
        """
        从最近的消息中提取决策

        Args:
            messages: 消息列表 (包含 role 和 content)
            project: 项目名称
            session_id: 会话 ID
            max_messages: 分析的最大消息数

        Returns:
            Decision 对象或 None（如果没有检测到决策）
        """
        if not messages:
            return None

        # 只取最近的消息
        recent_messages = messages[-max_messages:]

        # 格式化对话
        conversation = self._format_conversation(recent_messages)
        if not conversation.strip():
            return None

        prompt = DECISION_EXTRACTION_PROMPT.format(conversation=conversation)

        try:
            logger.debug(f"Extracting decision from {len(recent_messages)} messages")
            response = self.llm_client.generate(prompt)

            # 解析 JSON 响应
            result = self._parse_response(response)
            if not result or not result.get("has_decision"):
                logger.debug("No decision detected in conversation")
                return None

            # 创建 Decision 对象
            decision = Decision(
                id=None,
                project=project,
                session_id=session_id,
                problem=result.get("problem", ""),
                solution=result.get("solution", ""),
                status="pending",
                reason="",
                reason_options=json.dumps(result.get("reason_options", []), ensure_ascii=False),
                note="",
                files=json.dumps(result.get("files", []), ensure_ascii=False),
                tags="[]",
                timestamp=datetime.now(),
            )

            logger.info(f"Decision extracted: {decision.problem[:50]}...")
            return decision

        except Exception as e:
            logger.error(f"Error extracting decision: {e}")
            return None

    def _format_conversation(self, messages: list) -> str:
        """格式化消息为对话文本"""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # 如果 content 是 JSON 数组，提取文本部分
            if isinstance(content, str) and content.startswith("["):
                try:
                    parts = json.loads(content)
                    text_parts = []
                    for part in parts:
                        if isinstance(part, dict):
                            if part.get("type") == "text":
                                text_parts.append(part.get("content", ""))
                            elif part.get("type") == "tool":
                                tool_name = part.get("name", "tool")
                                text_parts.append(f"[Used {tool_name}]")
                    content = " ".join(text_parts)
                except json.JSONDecodeError:
                    pass

            # 截断过长的内容
            if len(content) > 1000:
                content = content[:1000] + "..."

            if content.strip():
                role_label = "User" if role == "user" else "Assistant"
                lines.append(f"{role_label}: {content}")

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

        # 尝试找到任何 JSON 对象
        json_match = re.search(r'\{[^{}]*"has_decision"[^{}]*\}', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        logger.warning(f"Failed to parse decision response: {response[:200]}...")
        return None
