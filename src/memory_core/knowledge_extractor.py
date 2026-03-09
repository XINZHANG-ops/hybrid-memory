import json
from loguru import logger
from .models import Message
from .llm_client import LLMClient
from .prompts import EXTRACTION_PROMPT, CONDENSE_PROMPT, CATEGORY_NAMES, ROLE_LABELS, UI_TEXT
from .content_processor import ContentConfig, process_content


class KnowledgeExtractor:
    def __init__(
        self,
        llm_client: LLMClient,
        content_config: ContentConfig | None = None,
        extraction_prompt: str = "",
        condense_prompt: str = "",
    ):
        self.llm = llm_client
        self.content_config = content_config or ContentConfig()
        self.extraction_prompt = extraction_prompt
        self.condense_prompt = condense_prompt
        logger.info(f"KnowledgeExtractor initialized (content_config={self.content_config})")

    def extract(self, messages: list[Message], existing_knowledge: dict | None = None) -> dict:
        if not messages:
            return self._empty_knowledge()

        conversation = self._format_conversation(messages)
        existing_str = self._format_existing_knowledge(existing_knowledge)

        if self.extraction_prompt and self.extraction_prompt.strip():
            try:
                prompt = self.extraction_prompt.format(
                    conversation=conversation,
                    existing_knowledge=existing_str
                )
                logger.debug("Using custom extraction prompt")
            except KeyError as e:
                logger.warning(f"Custom extraction prompt format error: {e}, falling back to default")
                prompt = EXTRACTION_PROMPT.format(conversation=conversation, existing_knowledge=existing_str)
        else:
            prompt = EXTRACTION_PROMPT.format(conversation=conversation, existing_knowledge=existing_str)

        try:
            response = self.llm.generate(prompt)
            knowledge = self._parse_response(response)
            logger.info(f"Extracted knowledge: {sum(len(v) for v in knowledge.values())} items")
            return knowledge
        except Exception as e:
            logger.error(f"Knowledge extraction failed: {e}")
            return self._empty_knowledge()

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

    def condense_knowledge(self, knowledge: dict, max_per_category: int = 10) -> dict:
        """当知识条目过多时，使用 LLM 精炼每个类别"""
        condensed = {}
        for key, items in knowledge.items():
            if len(items) <= max_per_category:
                condensed[key] = items
                continue

            # 需要精炼
            logger.info(f"Condensing {key}: {len(items)} -> {max_per_category}")
            prompt_template = self.condense_prompt if self.condense_prompt and self.condense_prompt.strip() else CONDENSE_PROMPT
            try:
                prompt = prompt_template.format(
                    category_name=CATEGORY_NAMES.get(key, key),
                    count=len(items),
                    items="\n".join(f"- {item}" for item in items),
                    max_count=max_per_category,
                )
            except KeyError as e:
                logger.warning(f"Custom condense prompt format error: {e}, falling back to default")
                prompt = CONDENSE_PROMPT.format(
                    category_name=CATEGORY_NAMES.get(key, key),
                    count=len(items),
                    items="\n".join(f"- {item}" for item in items),
                    max_count=max_per_category,
                )

            try:
                response = self.llm.generate(prompt)
                # 解析 JSON 数组
                response = response.strip()
                if response.startswith("```"):
                    lines = response.split("\n")
                    response = "\n".join(lines[1:-1])
                new_items = json.loads(response)
                if isinstance(new_items, list):
                    condensed[key] = new_items[:max_per_category]
                    logger.info(f"Condensed {key}: {len(items)} -> {len(condensed[key])}")
                else:
                    condensed[key] = items[:max_per_category]
            except Exception as e:
                logger.warning(f"Failed to condense {key}: {e}")
                condensed[key] = items[:max_per_category]

        return condensed
