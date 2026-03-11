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

_EXTRACTION_PROMPT_ZH = """你是一个**项目级知识管理专家**。你的任务是维护一个**超长期记忆库**，只保留最根本、最通用的项目级知识。

## 核心原则
这是一个**迭代演进**的知识库，每次对话都会融合新信息。你必须：
- **极度保守**：只提取那些在数周、数月后仍然有价值的知识
- **抽象优先**：提取原则和模式，而非具体实现细节
- **项目级视角**：关注架构决策、设计原则、用户长期偏好，忽略临时修复、具体bug、单次操作

## 什么应该保留
✅ 架构决策（如"使用 Flask + React 前端"）
✅ 设计原则（如"UI 组件统一复用 Summary 模板"）
✅ 用户长期偏好（如"偏好简洁终端风格，拒绝弹窗"）
✅ 技术栈选择（如"使用 SQLite 存储，FAISS 向量搜索"）
✅ 代码约定（如"路径处理统一使用相对路径"）

## 什么应该删除
❌ 具体 bug 修复（如"修复了 timestamp 格式问题"）
❌ 单次操作（如"添加了编辑按钮"）
❌ 临时状态（如"当前有 20 条待处理消息"）
❌ 会话特定细节（如"上一轮对话讨论了 X"）

## 已有知识：
{existing_knowledge}

## 新对话内容：
{conversation}

## 任务
融合已有知识和新对话，输出**精炼后的项目级知识库**。每个类别最多 {max_items} 条。

更新策略：
1. **保守添加**：只有真正项目级的知识才值得添加
2. **谨慎删除**：已有的抽象原则除非明确过时，否则保留
3. **向上抽象**：如果多个具体细节指向同一原则，提取原则而非罗列细节
4. **合并同类**：相似条目合并为更通用的描述

## 输出格式（JSON）：
```json
{{
  "user_preferences": ["用户的长期偏好、工作风格、原则性要求"],
  "architecture_decisions": ["技术栈、架构模式、系统设计决策"],
  "design_principles": ["代码约定、设计原则、一致性规则"],
  "learned_patterns": ["从项目中总结的通用模式和最佳实践"]
}}
```

请直接输出 JSON："""

_EXTRACTION_PROMPT_EN = """You are a **project-level knowledge management expert**. Your task is to maintain a **long-term memory store** that only keeps the most fundamental, general project-level knowledge.

## Core Principles
This is an **iteratively evolving** knowledge base that merges new information with each conversation. You must:
- **Be extremely conservative**: Only extract knowledge that will still be valuable weeks or months later
- **Prioritize abstraction**: Extract principles and patterns, not specific implementation details
- **Project-level perspective**: Focus on architecture decisions, design principles, long-term user preferences; ignore temporary fixes, specific bugs, one-time operations

## What to KEEP
✅ Architecture decisions (e.g., "Using Flask + React frontend")
✅ Design principles (e.g., "UI components should reuse Summary template")
✅ Long-term user preferences (e.g., "Prefer minimal terminal style, no popups")
✅ Tech stack choices (e.g., "Using SQLite storage, FAISS vector search")
✅ Code conventions (e.g., "Path handling uses relative paths")

## What to REMOVE
❌ Specific bug fixes (e.g., "Fixed timestamp format issue")
❌ One-time operations (e.g., "Added edit button")
❌ Temporary states (e.g., "Currently 20 pending messages")
❌ Session-specific details (e.g., "Last conversation discussed X")

## Existing Knowledge:
{existing_knowledge}

## New Conversation:
{conversation}

## Task
Merge existing knowledge with new conversation to output a **refined project-level knowledge base**. Maximum {max_items} items per category.

Update Strategy:
1. **Conservative addition**: Only truly project-level knowledge deserves to be added
2. **Cautious deletion**: Keep existing abstract principles unless clearly outdated
3. **Abstract upward**: If multiple specific details point to the same principle, extract the principle instead of listing details
4. **Merge similar**: Combine similar entries into more general descriptions

## Output Format (JSON):
```json
{{
  "user_preferences": ["User's long-term preferences, work style, principled requirements"],
  "architecture_decisions": ["Tech stack, architecture patterns, system design decisions"],
  "design_principles": ["Code conventions, design principles, consistency rules"],
  "learned_patterns": ["General patterns and best practices learned from the project"]
}}
```

Please output JSON directly:"""

# ============ Decision Extraction Prompts ============

_DECISION_EXTRACTION_PROMPT_ZH = """分析以下对话，提取所有**决策点** - 即识别了问题并选择了解决方案的时刻。

决策点包含：
1. 遇到的问题或难题
2. 采取的解决方案或方法
3. 选择的原因
4. 相关的文件（从下方文件列表中选择）

返回决策的 JSON 数组：
```json
{{
  "decisions": [
    {{
      "problem": "问题的简要描述（1-2句话）",
      "solution": "采取的解决方案（1-2句话）",
      "reason_options": ["可能原因1", "可能原因2", "可能原因3"],
      "files": ["src/xxx/file1.py", "src/yyy/file2.js"]
    }}
  ]
}}
```

如果没有决策点，返回：
```json
{{
  "decisions": []
}}
```

指南：
- 提取所有决策，可能有0个、1个或多个
- 关注技术决策，而非日常操作
- 决策涉及在多个选项中做出选择或解决非平凡问题
- reason_options 应包含2-4个合理的原因（用户将选择正确的）
- files 必须从下方"涉及的文件"列表中选择，使用完整路径（最多3个）
- 保持 problem 和 solution 简洁

涉及的文件：
{touched_files}

对话内容：
{conversation}

提取决策（仅输出 JSON）："""

_DECISION_EXTRACTION_PROMPT_EN = """Analyze the following conversation and extract ALL decision points - moments where a problem was identified and a solution was chosen.

A decision point includes:
1. A problem or issue that was encountered
2. A solution or approach that was taken
3. The reasoning behind the choice
4. Related files (select from the file list below)

Return a JSON array of decisions:
```json
{{
  "decisions": [
    {{
      "problem": "Brief description of the problem (1-2 sentences)",
      "solution": "Brief description of the solution taken (1-2 sentences)",
      "reason_options": ["Possible reason 1", "Possible reason 2", "Possible reason 3"],
      "files": ["src/xxx/file1.py", "src/yyy/file2.js"]
    }}
  ]
}}
```

If there are NO decision points, return:
```json
{{
  "decisions": []
}}
```

Guidelines:
- Extract ALL decisions, there may be 0, 1, or multiple
- Focus on technical decisions, not routine actions
- A decision involves choosing between alternatives or solving a non-trivial problem
- reason_options should be 2-4 plausible reasons (the user will select the correct one)
- files MUST be selected from the "Touched Files" list below, use full paths (3 files max)
- Keep problem and solution concise

Touched Files:
{touched_files}

Conversation:
{conversation}

Extract decisions (JSON only):"""

# ============ Decision Regenerate Prompts (Single) ============

_DECISION_REGENERATE_PROMPT_ZH = """你是一个技术决策分析专家。以下是一段对话历史和一个已识别的技术决策。
请针对这个决策，重新分析并生成更好的解决方案选项。

## 对话历史
{conversation}

## 涉及的文件
{touched_files}

## 当前决策
问题：{original_problem}
解决方案：{original_solution}

## 任务
针对上述问题，请：
1. 重新分析问题的本质（可以优化问题描述）
2. 生成一个更完善的解决方案
3. 提供 2-4 个可能的原因选项供用户选择
4. 从文件列表中选择相关文件（最多3个）

请以 JSON 格式返回：
```json
{{
  "problem": "问题描述（可以优化原问题的表述）",
  "solution": "推荐的解决方案",
  "reason_options": ["原因选项1", "原因选项2", "原因选项3"],
  "files": ["相关文件路径1", "相关文件路径2"]
}}
```

仅输出 JSON："""

_DECISION_REGENERATE_PROMPT_EN = """You are a technical decision analysis expert. Below is a conversation history and an identified technical decision.
Please re-analyze this decision and generate better solution options.

## Conversation History
{conversation}

## Touched Files
{touched_files}

## Current Decision
Problem: {original_problem}
Solution: {original_solution}

## Task
For the above problem, please:
1. Re-analyze the essence of the problem (you may improve the problem description)
2. Generate a more refined solution
3. Provide 2-4 possible reason options for the user to choose from
4. Select related files from the file list (max 3)

Return in JSON format:
```json
{{
  "problem": "Problem description (may improve the original wording)",
  "solution": "Recommended solution",
  "reason_options": ["Reason option 1", "Reason option 2", "Reason option 3"],
  "files": ["Related file path 1", "Related file path 2"]
}}
```

Output JSON only:"""

# ============ Category Names ============

_CATEGORY_NAMES_ZH = {
    "user_preferences": "用户偏好",
    "architecture_decisions": "架构决策",
    "design_principles": "设计原则",
    "learned_patterns": "行为模式",
    # 兼容旧数据
    "project_decisions": "项目决策",
    "key_facts": "关键事实",
    "pending_tasks": "待办事项",
    "important_context": "重要上下文",
}

_CATEGORY_NAMES_EN = {
    "user_preferences": "User Preferences",
    "architecture_decisions": "Architecture Decisions",
    "design_principles": "Design Principles",
    "learned_patterns": "Learned Patterns",
    # Legacy compatibility
    "project_decisions": "Project Decisions",
    "key_facts": "Key Facts",
    "pending_tasks": "Pending Tasks",
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
        "decision": _DECISION_EXTRACTION_PROMPT_ZH,
        "decision_regenerate": _DECISION_REGENERATE_PROMPT_ZH,
        "category_names": _CATEGORY_NAMES_ZH,
        "role_labels": _ROLE_LABELS_ZH,
        "ui_text": _UI_TEXT_ZH,
    },
    "en": {
        "summary_with_context": _SUMMARY_PROMPT_WITH_CONTEXT_EN,
        "summary": _SUMMARY_PROMPT_EN,
        "extraction": _EXTRACTION_PROMPT_EN,
        "decision": _DECISION_EXTRACTION_PROMPT_EN,
        "decision_regenerate": _DECISION_REGENERATE_PROMPT_EN,
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
    def DECISION_PROMPT(self):
        return get_prompt("decision")

    @property
    def DECISION_REGENERATE_PROMPT(self):
        return get_prompt("decision_regenerate")

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
