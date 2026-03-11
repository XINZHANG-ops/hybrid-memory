import json
from loguru import logger
from .models import Message
from .llm_client import LLMClient
from .prompts import EXTRACTION_PROMPT, CATEGORY_NAMES, ROLE_LABELS, UI_TEXT
from .content_processor import ContentConfig, process_content


class KnowledgeExtractor:
    def __init__(
        self,
        llm_client: LLMClient,
        content_config: ContentConfig | None = None,
        extraction_prompt: str = "",
        max_items_per_category: int = 10,
    ):
        self.llm = llm_client
        self.content_config = content_config or ContentConfig()
        self.extraction_prompt = extraction_prompt
        self.max_items = max_items_per_category
        logger.info(f"KnowledgeExtractor initialized (max_items={self.max_items}, content_config={self.content_config})")

    def extract(self, messages: list[Message], existing_knowledge: dict | None = None) -> dict:
        """
        从对话中提取知识，与已有知识融合。
        LLM 直接输出融合后的完整知识库（每类最多 max_items 条）。
        """
        if not messages:
            return existing_knowledge or self._empty_knowledge()

        conversation = self._format_conversation(messages)
        existing_str = self._format_existing_knowledge(existing_knowledge)

        if self.extraction_prompt and self.extraction_prompt.strip():
            try:
                prompt = self.extraction_prompt.format(
                    conversation=conversation,
                    existing_knowledge=existing_str,
                    max_items=self.max_items
                )
                logger.debug("Using custom extraction prompt")
            except KeyError as e:
                logger.warning(f"Custom extraction prompt format error: {e}, falling back to default")
                prompt = EXTRACTION_PROMPT.format(
                    conversation=conversation,
                    existing_knowledge=existing_str,
                    max_items=self.max_items
                )
        else:
            prompt = EXTRACTION_PROMPT.format(
                conversation=conversation,
                existing_knowledge=existing_str,
                max_items=self.max_items
            )

        try:
            response = self.llm.generate(prompt)
            knowledge = self._parse_response(response)
            # 确保每类不超过 max_items
            for key in knowledge:
                if len(knowledge[key]) > self.max_items:
                    knowledge[key] = knowledge[key][:self.max_items]
            logger.info(f"Extracted knowledge: {sum(len(v) for v in knowledge.values())} items")
            return knowledge
        except Exception as e:
            logger.error(f"Knowledge extraction failed: {e}")
            return existing_knowledge or self._empty_knowledge()

    def _format_existing_knowledge(self, knowledge: dict | None) -> str:
        no_knowledge_text = UI_TEXT.get("no_existing_knowledge", "(No existing knowledge)")
        if not knowledge:
            return no_knowledge_text
        lines = []
        for key, items in knowledge.items():
            if items:
                name = CATEGORY_NAMES.get(key, key)
                lines.append(f"- {name}: {', '.join(items[:10])}")
        return "\n".join(lines) if lines else no_knowledge_text

    def _format_conversation(self, messages: list[Message]) -> str:
        lines = []
        for msg in messages:
            role_label = ROLE_LABELS.get(msg.role, msg.role)
            content = process_content(msg.content, self.content_config)
            if not content:
                continue  # 用户关闭了所有类型，跳过此消息
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

            # 验证结构（新类别 + 兼容旧数据）
            valid_keys = ["user_preferences", "architecture_decisions", "design_principles",
                         "learned_patterns", "project_decisions", "key_facts",
                         "pending_tasks", "important_context"]
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
            "architecture_decisions": [],
            "design_principles": [],
            "learned_patterns": [],
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

