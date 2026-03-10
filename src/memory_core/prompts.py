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

_EXTRACTION_PROMPT_ZH = """你是一个知识管理助手。请根据已有知识和新对话，输出**融合后的**结构化知识。

## 已有知识：
{existing_knowledge}

## 新对话内容：
{conversation}

## 任务
结合已有知识和新对话，输出**融合后的完整知识库**。每个类别最多 {max_items} 条。

要求：
1. **融合**：将已有知识与新对话中的信息合并
2. **更新**：如果新对话修正了已有知识，使用新版本
3. **去重**：合并相似或重复的条目
4. **精简**：删除过时或不再相关的内容
5. **限制**：每类不超过 {max_items} 条，保留最重要的

## 输出格式（JSON）：
```json
{{
  "user_preferences": ["用户偏好和习惯"],
  "project_decisions": ["技术决策和架构选择"],
  "key_facts": ["关键事实"],
  "pending_tasks": ["待办事项，已完成的用[已完成]标注"],
  "learned_patterns": ["行为模式"],
  "important_context": ["重要上下文"]
}}
```

请直接输出 JSON："""

_EXTRACTION_PROMPT_EN = """You are a knowledge management assistant. Based on existing knowledge and new conversation, output **merged** structured knowledge.

## Existing Knowledge:
{existing_knowledge}

## New Conversation:
{conversation}

## Task
Combine existing knowledge with new conversation to output a **complete merged knowledge base**. Maximum {max_items} items per category.

Requirements:
1. **Merge**: Combine existing knowledge with information from new conversation
2. **Update**: If new conversation corrects existing knowledge, use the new version
3. **Deduplicate**: Merge similar or duplicate entries
4. **Prune**: Remove outdated or no longer relevant content
5. **Limit**: No more than {max_items} items per category, keep the most important

## Output Format (JSON):
```json
{{
  "user_preferences": ["User preferences and habits"],
  "project_decisions": ["Technical decisions and architectural choices"],
  "key_facts": ["Key facts"],
  "pending_tasks": ["Pending tasks, mark completed ones with [Completed]"],
  "learned_patterns": ["Behavior patterns"],
  "important_context": ["Important context"]
}}
```

Please output JSON directly:"""

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

# ============ UI Text ============

_UI_TEXT_ZH = {
    "no_existing_knowledge": "(无已有知识)",
}

_UI_TEXT_EN = {
    "no_existing_knowledge": "(No existing knowledge)",
}

# ============ Prompt Data by Language ============

_PROMPTS = {
    "zh": {
        "summary_with_context": _SUMMARY_PROMPT_WITH_CONTEXT_ZH,
        "summary": _SUMMARY_PROMPT_ZH,
        "extraction": _EXTRACTION_PROMPT_ZH,
        "category_names": _CATEGORY_NAMES_ZH,
        "role_labels": _ROLE_LABELS_ZH,
        "ui_text": _UI_TEXT_ZH,
    },
    "en": {
        "summary_with_context": _SUMMARY_PROMPT_WITH_CONTEXT_EN,
        "summary": _SUMMARY_PROMPT_EN,
        "extraction": _EXTRACTION_PROMPT_EN,
        "category_names": _CATEGORY_NAMES_EN,
        "role_labels": _ROLE_LABELS_EN,
        "ui_text": _UI_TEXT_EN,
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
    def CATEGORY_NAMES(self):
        return get_prompt("category_names")

    @property
    def ROLE_LABELS(self):
        return get_prompt("role_labels")

    @property
    def UI_TEXT(self):
        return get_prompt("ui_text")


_accessor = _PromptAccessor()

# 导出兼容旧代码的变量（通过 __getattr__ 动态获取）
def __getattr__(name):
    if hasattr(_accessor, name):
        return getattr(_accessor, name)
    raise AttributeError(f"module 'prompts' has no attribute '{name}'")
