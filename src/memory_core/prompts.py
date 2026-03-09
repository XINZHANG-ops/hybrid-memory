"""
统一 Prompt 管理模块
所有 LLM prompt 模板集中定义在此
"""

# ============ Summary Prompts ============

SUMMARY_PROMPT_WITH_CONTEXT = """# 任务：总结对话

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

SUMMARY_PROMPT = """# 任务：总结对话

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

# ============ Knowledge Extraction Prompts ============

EXTRACTION_PROMPT = """你是一个知识提取助手。请从以下对话中提取**新的**结构化知识点。

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

CONDENSE_PROMPT = """你是一个知识精炼助手。以下是某个类别的知识条目列表，数量过多需要精炼。

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

# ============ Category Names (for display) ============

CATEGORY_NAMES = {
    "user_preferences": "用户偏好",
    "project_decisions": "项目决策",
    "key_facts": "关键事实",
    "pending_tasks": "待办事项",
    "learned_patterns": "行为模式",
    "important_context": "重要上下文",
}

# ============ Role Labels ============

ROLE_LABELS = {
    "user": "用户",
    "assistant": "助手",
    "system": "系统",
}
