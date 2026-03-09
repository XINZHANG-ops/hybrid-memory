"""
统一 Prompt 管理模块
所有 LLM prompt 模板集中定义在此
支持从 config 动态读取语言设置
"""
from pathlib import Path

# ============ Summary Prompts ============

_SUMMARY_PROMPT_WITH_CONTEXT_ZH = """# 任务：总结对话

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

_SUMMARY_PROMPT_WITH_CONTEXT_EN = """# Task: Summarize Conversation

You are a summarization assistant, not a conversation participant. Do not continue the conversation, just output the summary.

## Historical Context
{previous_context}

## Conversation to Summarize
{conversation}

## Output Requirements
Output the summary text directly (do not output "Assistant:" or any role labels), including:
1. Main topics
2. Important decisions
3. Pending tasks
4. Key context

Use concise English, no more than 500 words."""

_SUMMARY_PROMPT_ZH = """# 任务：总结对话

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

_SUMMARY_PROMPT_EN = """# Task: Summarize Conversation

You are a summarization assistant, not a conversation participant. Do not continue the conversation, just output the summary.

## Conversation to Summarize
{conversation}

## Output Requirements
Output the summary text directly (do not output "Assistant:" or any role labels), including:
1. Main topics
2. Important decisions
3. Pending tasks
4. Key context

Use concise English, no more than 500 words."""

# ============ Knowledge Extraction Prompts ============

_EXTRACTION_PROMPT_ZH = """你是一个知识提取助手。请从以下对话中提取**新的**结构化知识点。

## 已有知识（删除过时或不再相关的内容， 合并相似或重复的条目）：
{existing_knowledge}

## 新对话内容：
{conversation}

## 请提取以下类型的**新**知识（JSON格式）：

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
- **不要重复已有知识中的内容**，只提取新信息
- 如果新对话修正或更新了已有知识，提取更新后的版本
- 如果某个待办事项已完成，可以在 pending_tasks 中标注"[已完成] xxx"
- 只提取确实存在的信息，没有的字段留空数组
- 每个条目应该简洁明了
- 用中文输出

请输出 JSON（只输出 JSON，不要其他内容）："""

_EXTRACTION_PROMPT_EN = """You are a knowledge extraction assistant. Please extract **new** structured knowledge points from the following conversation.

## Existing Knowledge (remove outdated or irrelevant content, merge similar or duplicate entries):
{existing_knowledge}

## New Conversation:
{conversation}

## Please extract the following types of **new** knowledge (JSON format):

```json
{{
  "user_preferences": ["User preferences and habits, such as coding style, communication style, etc."],
  "project_decisions": ["Important technical decisions and architectural choices made in the project"],
  "key_facts": ["Key facts to remember, such as project name, tech stack, username, etc."],
  "pending_tasks": ["Tasks mentioned but not completed, or to-do items"],
  "learned_patterns": ["Observed user behavior patterns or work styles"],
  "important_context": ["Other important contextual information"]
}}
```

Notes:
- **Do not repeat content from existing knowledge**, only extract new information
- If the new conversation corrects or updates existing knowledge, extract the updated version
- If a pending task is completed, mark it as "[Completed] xxx" in pending_tasks
- Only extract information that actually exists, leave empty arrays for missing fields
- Each entry should be concise and clear
- Output in English

Please output JSON (only JSON, no other content):"""

_CONDENSE_PROMPT_ZH = """你是一个知识精炼助手。以下是某个类别的知识条目列表，数量过多需要精炼。

## 类别：{category_name}
## 当前条目（{count}条）：
{items}

## 要求
请将这些条目精炼为不超过 {max_count} 条，要求：
1. 合并相似或重复的条目
2. 保留最重要、最有价值的信息
3. 删除过时或不再相关的内容
4. 每条保持简洁明了

请直接输出精炼后的条目列表（JSON数组格式），例如：
["条目1", "条目2", "条目3"]

只输出 JSON 数组，不要其他内容："""

_CONDENSE_PROMPT_EN = """You are a knowledge condensation assistant. The following is a list of knowledge entries for a category that has too many items and needs to be condensed.

## Category: {category_name}
## Current Entries ({count} items):
{items}

## Requirements
Please condense these entries to no more than {max_count} items:
1. Merge similar or duplicate entries
2. Keep the most important and valuable information
3. Remove outdated or no longer relevant content
4. Keep each entry concise and clear

Please output the condensed list directly (JSON array format), for example:
["Entry 1", "Entry 2", "Entry 3"]

Only output JSON array, no other content:"""

# ============ Category Names ============

_CATEGORY_NAMES_ZH = {
    "user_preferences": "用户偏好",
    "project_decisions": "项目决策",
    "key_facts": "关键事实",
    "pending_tasks": "待办事项",
    "learned_patterns": "行为模式",
    "important_context": "重要上下文",
}

_CATEGORY_NAMES_EN = {
    "user_preferences": "User Preferences",
    "project_decisions": "Project Decisions",
    "key_facts": "Key Facts",
    "pending_tasks": "Pending Tasks",
    "learned_patterns": "Learned Patterns",
    "important_context": "Important Context",
}

# ============ Role Labels ============

_ROLE_LABELS_ZH = {
    "user": "用户",
    "assistant": "助手",
    "system": "系统",
}

_ROLE_LABELS_EN = {
    "user": "User",
    "assistant": "Assistant",
    "system": "System",
}

# ============ Prompt Data by Language ============

_PROMPTS = {
    "zh": {
        "summary_with_context": _SUMMARY_PROMPT_WITH_CONTEXT_ZH,
        "summary": _SUMMARY_PROMPT_ZH,
        "extraction": _EXTRACTION_PROMPT_ZH,
        "condense": _CONDENSE_PROMPT_ZH,
        "category_names": _CATEGORY_NAMES_ZH,
        "role_labels": _ROLE_LABELS_ZH,
    },
    "en": {
        "summary_with_context": _SUMMARY_PROMPT_WITH_CONTEXT_EN,
        "summary": _SUMMARY_PROMPT_EN,
        "extraction": _EXTRACTION_PROMPT_EN,
        "condense": _CONDENSE_PROMPT_EN,
        "category_names": _CATEGORY_NAMES_EN,
        "role_labels": _ROLE_LABELS_EN,
    },
}

# ============ Global DB Path ============
_GLOBAL_DB_PATH = Path(__file__).parent.parent.parent / "data" / "global_memory.db"


def _get_language() -> str:
    """从 config 动态获取语言设置"""
    try:
        import sqlite3
        if not _GLOBAL_DB_PATH.exists():
            return "zh"
        conn = sqlite3.connect(_GLOBAL_DB_PATH)
        cursor = conn.execute("SELECT value FROM config WHERE key = 'prompt_language'")
        row = cursor.fetchone()
        conn.close()
        if row and row[0] in ("zh", "en"):
            return row[0]
    except Exception:
        pass
    return "zh"


def get_prompt(key: str) -> str | dict:
    """获取指定 key 的 prompt（根据当前语言设置）"""
    lang = _get_language()
    return _PROMPTS.get(lang, _PROMPTS["zh"]).get(key, "")


# ============ 兼容旧代码的属性访问 ============

class _PromptAccessor:
    """动态属性访问器，支持 prompts.SUMMARY_PROMPT 这样的旧式访问"""

    @property
    def SUMMARY_PROMPT_WITH_CONTEXT(self):
        return get_prompt("summary_with_context")

    @property
    def SUMMARY_PROMPT(self):
        return get_prompt("summary")

    @property
    def EXTRACTION_PROMPT(self):
        return get_prompt("extraction")

    @property
    def CONDENSE_PROMPT(self):
        return get_prompt("condense")

    @property
    def CATEGORY_NAMES(self):
        return get_prompt("category_names")

    @property
    def ROLE_LABELS(self):
        return get_prompt("role_labels")


_accessor = _PromptAccessor()

# 导出兼容旧代码的变量（通过 __getattr__ 动态获取）
def __getattr__(name):
    if hasattr(_accessor, name):
        return getattr(_accessor, name)
    raise AttributeError(f"module 'prompts' has no attribute '{name}'")
