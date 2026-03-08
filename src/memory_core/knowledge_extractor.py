import json
from loguru import logger
from .models import Message
from .llm_client import LLMClient


EXTRACTION_PROMPT = """你是一个知识提取助手。请从以下对话中提取结构化的知识点。

## 对话内容：
{conversation}

## 请提取以下类型的知识（JSON格式）：

```json
{{
  "user_preferences": ["用户的偏好和习惯，如编码风格、沟通方式等"],
  "project_decisions": ["项目中做出的重要技术决策和架构选择"],
  "key_facts": ["需要记住的关键事实，如项目名、技术栈、用户名等"],
  "pending_tasks": ["提到但未完成的任务或待办事项"],
  "learned_patterns": ["观察到的用户行为模式或工作方式"],
  "important_context": ["其他重要的上下文信息"]
}}
```

注意：
- 只提取确实存在的信息，没有的字段留空数组
- 每个条目应该简洁明了
- 避免重复已知信息
- 用中文输出

请输出 JSON（只输出 JSON，不要其他内容）："""


class KnowledgeExtractor:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        logger.info("KnowledgeExtractor initialized")

    def extract(self, messages: list[Message]) -> dict:
        if not messages:
            return self._empty_knowledge()

        conversation = self._format_conversation(messages)
        prompt = EXTRACTION_PROMPT.format(conversation=conversation)

        try:
            response = self.llm.generate(prompt)
            knowledge = self._parse_response(response)
            logger.info(f"Extracted knowledge: {sum(len(v) for v in knowledge.values())} items")
            return knowledge
        except Exception as e:
            logger.error(f"Knowledge extraction failed: {e}")
            return self._empty_knowledge()

    def _format_conversation(self, messages: list[Message]) -> str:
        lines = []
        for msg in messages:
            role_label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(msg.role, msg.role)
            content = msg.content[:500] if len(msg.content) > 500 else msg.content
            lines.append(f"{role_label}: {content}")
        return "\n".join(lines)

    def _parse_response(self, response: str) -> dict:
        try:
            # 尝试提取 JSON 块
            if "```json" in response:
                start = response.find("```json") + 7
                end = response.find("```", start)
                response = response[start:end].strip()
            elif "```" in response:
                start = response.find("```") + 3
                end = response.find("```", start)
                response = response[start:end].strip()

            knowledge = json.loads(response)

            # 验证结构
            valid_keys = ["user_preferences", "project_decisions", "key_facts",
                         "pending_tasks", "learned_patterns", "important_context"]
            result = {k: knowledge.get(k, []) for k in valid_keys}

            # 确保每个值都是列表
            for k in result:
                if not isinstance(result[k], list):
                    result[k] = [result[k]] if result[k] else []

            return result
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse knowledge JSON: {e}")
            return self._empty_knowledge()

    def _empty_knowledge(self) -> dict:
        return {
            "user_preferences": [],
            "project_decisions": [],
            "key_facts": [],
            "pending_tasks": [],
            "learned_patterns": [],
            "important_context": []
        }

    def merge_knowledge(self, existing: dict, new: dict) -> dict:
        """合并新旧知识，去重"""
        merged = {}
        for key in existing:
            existing_items = set(existing.get(key, []))
            new_items = set(new.get(key, []))
            merged[key] = list(existing_items | new_items)
        logger.debug(f"Merged knowledge: {sum(len(v) for v in merged.values())} total items")
        return merged
