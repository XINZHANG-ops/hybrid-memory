"""
配置管理模块 - 从数据库读取/保存配置
"""
from pathlib import Path
from typing import Any
from .database import Database

# 默认配置
DEFAULT_CONFIG = {
    "short_term_window_size": "20",
    "max_context_tokens": "8000",
    "summary_trigger_threshold": "50",
    "llm_provider": "ollama",
    "ollama_model": "qwen3:8b",
    "ollama_base_url": "http://localhost:11434",
    "ollama_timeout": "300",
    "ollama_keep_alive": "10m",
    "anthropic_model": "claude-sonnet-4-20250514",
    "embedding_model": "embeddinggemma:300m",
    "embedding_base_url": "",  # 空=使用 ollama_base_url
    "enable_vector_search": "true",
    "enable_knowledge_extraction": "true",
    "input_token_price": "0.003",
    "output_token_price": "0.015",
    # 注入相关配置
    "inject_summary_count": "5",
    "inject_recent_count": "5",
    "inject_preview_length": "200",
    "inject_knowledge_count": "5",
    "inject_task_count": "3",
    # 总结管理配置
    "selected_summary_ids": "{}",  # JSON: {"project_name": [1, 3, 5], ...}
    "summary_prompt_template": "",  # 空=使用默认模板
    # 知识提取 Prompt
    "knowledge_extraction_prompt": "",  # 空=使用默认模板
    "knowledge_condense_prompt": "",  # 空=使用默认模板
    # 总结生成配置
    "summary_max_chars_total": "8000",
    "summary_max_chars_per_message": "500",
    # 知识提取配置
    "knowledge_max_chars_per_message": "500",
    "knowledge_max_items_per_category": "10",
    "knowledge_auto_condense": "true",
    # 搜索结果配置
    "search_result_preview_length": "500",
    # Dashboard 刷新配置
    "dashboard_refresh_interval": "5000",
    # Prompt 语言配置
    "prompt_language": "zh",
}

# 配置元数据（用于 UI 展示）
# tooltip: 鼠标悬停时显示的详细说明
CONFIG_META = {
    "short_term_window_size": {
        "label": "短期记忆消息数",
        "description": "保留最近多少条消息作为短期记忆",
        "tooltip": "【MCP get_context() 专用】当 Claude 主动调用 MCP 工具查询上下文时，返回当前 session 内最近 N 条消息。这是实时查询，可在对话中随时调用。与「注入近期对话数」的区别：本配置用于 MCP 工具返回值，限定单个 session；注入配置用于会话启动时的自动注入，跨所有 session。实际返回数量还受 max_context_tokens 限制。影响模块：ShortTermMemory",
        "type": "number",
        "min": 5,
        "max": 100,
        "group": "Memory",
    },
    "max_context_tokens": {
        "label": "最大上下文 Token",
        "description": "短期记忆的最大 token 数",
        "tooltip": "限制 get_context() 返回的短期记忆消息总 token 数。系统会从最新消息开始累加，直到达到此限制。注意：这只限制短期记忆部分，不包括历史摘要和结构化知识。影响模块：ShortTermMemory.get_within_token_limit()",
        "type": "number",
        "min": 1000,
        "max": 32000,
        "group": "Memory",
    },
    "summary_trigger_threshold": {
        "label": "总结触发阈值",
        "description": "未总结消息达到多少条时自动触发总结",
        "tooltip": "当所有会话中未被总结的消息总数达到此阈值时，系统会自动调用 LLM 生成摘要。总结后这些消息会被标记为已总结。建议值：消息较短时可设大些（50-100），消息较长时设小些（10-30）。影响模块：LongTermMemory.should_summarize()",
        "type": "number",
        "min": 10,
        "max": 200,
        "group": "Memory",
    },
    "llm_provider": {
        "label": "LLM 提供者",
        "description": "用于生成摘要和提取知识的 LLM",
        "tooltip": "选择使用哪个 LLM 服务来生成摘要和提取结构化知识。ollama 为本地模型（免费但需要自行部署），anthropic 为 Claude API（需要 API Key）。影响模块：SummaryGenerator, KnowledgeExtractor",
        "type": "select",
        "options": ["ollama", "anthropic"],
        "group": "LLM",
    },
    "ollama_model": {
        "label": "Ollama 模型",
        "description": "Ollama 使用的模型名称",
        "tooltip": "指定 Ollama 使用的模型，如 qwen3:8b, llama3:8b, mistral 等。需要先用 ollama pull <model> 下载。较大的模型效果更好但速度更慢。推荐：qwen3:8b 或 qwen3:14b",
        "type": "text",
        "group": "LLM",
    },
    "ollama_base_url": {
        "label": "Ollama 地址",
        "description": "Ollama 服务的 HTTP 地址",
        "tooltip": "Ollama 服务的访问地址。本地默认是 http://localhost:11434。如果 Ollama 运行在其他机器或 Docker 中，需要相应修改。",
        "type": "text",
        "group": "LLM",
    },
    "ollama_timeout": {
        "label": "Ollama 超时(秒)",
        "description": "API 请求超时时间",
        "tooltip": "等待 Ollama 响应的最大时间。大模型或长文本需要更长时间。如果经常超时，可以增大此值。首次请求（模型加载）通常需要更长时间。",
        "type": "number",
        "min": 60,
        "max": 600,
        "group": "LLM",
    },
    "ollama_keep_alive": {
        "label": "Ollama Keep Alive",
        "description": "模型在内存中保持的时间",
        "tooltip": "Ollama 模型加载后在内存中保持多久。格式：5m（5分钟）、1h（1小时）、-1（永不卸载）。设置较长时间可以避免重复加载，但会占用显存。",
        "type": "text",
        "group": "LLM",
    },
    "anthropic_model": {
        "label": "Anthropic 模型",
        "description": "使用 Anthropic API 时的模型名称",
        "tooltip": "当 llm_provider 设为 anthropic 时使用的模型。如 claude-sonnet-4-20250514。需要设置 ANTHROPIC_API_KEY 环境变量。",
        "type": "text",
        "group": "LLM",
    },
    "embedding_model": {
        "label": "Embedding 模型",
        "description": "用于向量搜索的 embedding 模型",
        "tooltip": "用于将文本转换为向量的模型，用于语义搜索。需要是 Ollama 支持的 embedding 模型。推荐：nomic-embed-text, mxbai-embed-large, embeddinggemma:300m。需要先 ollama pull 下载。更换模型后需点击「重建向量库」按钮。",
        "type": "text",
        "group": "Embedding",
    },
    "embedding_base_url": {
        "label": "Embedding 服务地址",
        "description": "Embedding 服务的 HTTP 地址（默认与 Ollama 相同）",
        "tooltip": "Embedding 服务的访问地址。通常与 Ollama 地址相同，如果使用独立的 embedding 服务可单独配置。留空则使用 Ollama 地址。",
        "type": "text",
        "group": "Embedding",
    },
    "enable_vector_search": {
        "label": "启用向量搜索",
        "description": "是否启用语义相似度搜索",
        "tooltip": "启用后，每条消息会生成 embedding 向量并存储，支持按语义相似度搜索。需要 embedding 模型可用。会增加存储空间和消息处理时间。",
        "type": "select",
        "options": ["true", "false"],
        "group": "Embedding",
    },
    "enable_knowledge_extraction": {
        "label": "启用知识提取",
        "description": "是否自动从对话中提取结构化知识",
        "tooltip": "启用后，每次生成摘要时会同时调用 LLM 提取结构化知识（用户偏好、项目决策等）。这些知识会在新会话开始时注入。会增加 LLM 调用次数。",
        "type": "select",
        "options": ["true", "false"],
        "group": "Knowledge",
    },
    "input_token_price": {
        "label": "Input Token 单价",
        "description": "每 1K input tokens 的价格（美元）",
        "tooltip": "用于计算 LLM 调用成本。这是 input tokens（发送给模型的内容）的单价。仅用于成本统计显示，不影响功能。",
        "type": "number",
        "min": 0,
        "max": 1,
        "group": "Stats",
    },
    "output_token_price": {
        "label": "Output Token 单价",
        "description": "每 1K output tokens 的价格（美元）",
        "tooltip": "用于计算 LLM 调用成本。这是 output tokens（模型生成的内容）的单价。仅用于成本统计显示，不影响功能。",
        "type": "number",
        "min": 0,
        "max": 1,
        "group": "Stats",
    },
    # 注入相关配置
    "inject_summary_count": {
        "label": "注入摘要数量",
        "description": "启动时自动注入的历史摘要数量",
        "tooltip": "新会话开始时，自动注入最近 N 条摘要作为历史背景。这些是系统自动选择的最新摘要。你还可以在 Dashboard 的 Summaries 页面手动选择额外的摘要。影响模块：sessionStart hook",
        "type": "number",
        "min": 1,
        "max": 20,
        "group": "Inject",
    },
    "inject_recent_count": {
        "label": "注入近期对话数",
        "description": "启动时注入的近期原始消息数量",
        "tooltip": "【sessionStart hook 专用】新会话启动时，自动注入跨所有 session 的最近 N 条原始消息到 system-reminder。这是启动时的一次性快照，让 Claude 快速了解最近聊了什么。与「短期记忆消息数」的区别：本配置用于会话启动时自动注入，跨所有 session；短期记忆用于 MCP 工具实时查询，限定单个 session。消息可能被 inject_preview_length 截断。影响模块：sessionStart hook",
        "type": "number",
        "min": 3,
        "max": 20,
        "group": "Inject",
    },
    "inject_preview_length": {
        "label": "注入消息截取长度",
        "description": "注入时每条消息的最大字符数",
        "tooltip": "注入近期对话时，每条消息最多显示多少字符。设为 0 表示不截取（显示完整内容）。较长的消息会被截断并添加 '...'。这只影响注入显示，不影响数据库存储。影响模块：sessionStart hook",
        "type": "number",
        "min": 0,
        "max": 2000,
        "group": "Inject",
    },
    "inject_knowledge_count": {
        "label": "注入知识项数量",
        "description": "每类知识注入时显示的条目数",
        "tooltip": "注入结构化知识时，每个类别（用户偏好、项目决策等）最多显示多少条。注意这只影响注入显示，完整知识存储在数据库中。如果需要限制总存储量，请使用 knowledge_max_items_per_category。影响模块：sessionStart hook",
        "type": "number",
        "min": 1,
        "max": 20,
        "group": "Inject",
    },
    "inject_task_count": {
        "label": "注入待办项数量",
        "description": "注入时显示的待办事项数量",
        "tooltip": "注入结构化知识中的「待办事项」类别时，最多显示多少条。待办事项通常是从对话中提取的未完成任务。影响模块：sessionStart hook",
        "type": "number",
        "min": 1,
        "max": 10,
        "group": "Inject",
    },
    "summary_prompt_template": {
        "label": "自定义总结 Prompt",
        "description": "自定义总结生成的 prompt 模板",
        "tooltip": "可用变量：{previous_context}（历史摘要）、{conversation}（当前对话）。留空则使用系统默认模板。自定义模板可以调整总结风格、语言、输出格式等。",
        "type": "textarea",
        "group": "Advanced",
    },
    "knowledge_extraction_prompt": {
        "label": "知识提取 Prompt",
        "description": "自定义知识提取的 prompt 模板",
        "tooltip": "可用变量：{existing_knowledge}（已有知识）、{conversation}（当前对话）。留空则使用系统默认模板。输出需为 JSON 格式，包含 user_preferences、project_decisions、key_facts、pending_tasks、learned_patterns、important_context 六个数组字段。",
        "type": "textarea",
        "group": "Advanced",
    },
    "knowledge_condense_prompt": {
        "label": "知识精炼 Prompt",
        "description": "自定义知识精炼的 prompt 模板",
        "tooltip": "可用变量：{category_name}（类别名称）、{count}（当前条目数）、{items}（条目列表）、{max_count}（目标条目数）。留空则使用系统默认模板。用于将过多的知识条目精炼合并。",
        "type": "textarea",
        "group": "Advanced",
    },
    # 总结生成配置
    "summary_max_chars_total": {
        "label": "总结对话最大字符",
        "description": "发送给 LLM 生成总结时的对话总字符数上限",
        "tooltip": "生成摘要时，对话内容的最大总字符数。系统会从最新消息开始累加，超过此限制的早期消息会被跳过（不发送给 LLM）。较大值可以让摘要覆盖更多内容，但会增加 token 消耗。Dashboard 的 Summary Messages 弹窗会显示哪些消息被包含/跳过。",
        "type": "number",
        "min": 2000,
        "max": 32000,
        "group": "Summary",
    },
    "summary_max_chars_per_message": {
        "label": "总结单条消息最大字符",
        "description": "发送给 LLM 时每条消息的最大字符数",
        "tooltip": "生成摘要时，每条消息最多保留多少字符。超过的部分会被截断。这是为了避免单条超长消息（如代码）占用过多 token。Dashboard 的 Summary Messages 弹窗会标注被截断的消息。",
        "type": "number",
        "min": 100,
        "max": 2000,
        "group": "Summary",
    },
    # 知识提取配置
    "knowledge_max_chars_per_message": {
        "label": "知识提取消息最大字符",
        "description": "提取知识时每条消息的最大字符数",
        "tooltip": "调用 LLM 提取结构化知识时，每条消息最多保留多少字符。与总结类似，用于控制发送给 LLM 的内容量。",
        "type": "number",
        "min": 100,
        "max": 2000,
        "group": "Knowledge",
    },
    "knowledge_max_items_per_category": {
        "label": "每类知识最大条目数",
        "description": "每个知识类别的条目数上限",
        "tooltip": "每个知识类别（用户偏好、项目决策、关键事实等）最多保留多少条。当条目数超过此限制时：如果 knowledge_auto_condense=true，会调用 LLM 将多条合并精炼为更少的条目；否则简单保留最新的条目。",
        "type": "number",
        "min": 3,
        "max": 50,
        "group": "Knowledge",
    },
    "knowledge_auto_condense": {
        "label": "自动精炼知识",
        "description": "条目过多时是否自动调用 LLM 精炼",
        "tooltip": "当某个知识类别的条目数超过 knowledge_max_items_per_category 时，如果启用此选项，会调用 LLM 将条目合并精炼（例如将 20 条相似的偏好合并为 10 条）。这样可以保留更多有价值的信息。缺点是会消耗额外 token。关闭则简单截断。",
        "type": "select",
        "options": ["true", "false"],
        "group": "Knowledge",
    },
    # 搜索结果配置
    "search_result_preview_length": {
        "label": "搜索结果预览长度",
        "description": "搜索结果中每条消息的最大显示字符数",
        "tooltip": "在 Dashboard 搜索结果中，每条消息内容最多显示多少字符。这只影响显示，不影响搜索算法。增大此值可以看到更多内容，但会增加页面渲染时间。",
        "type": "number",
        "min": 100,
        "max": 2000,
        "group": "Search",
    },
    # Dashboard 配置
    "dashboard_refresh_interval": {
        "label": "Dashboard 刷新间隔",
        "description": "自动刷新时间间隔（毫秒）",
        "tooltip": "Dashboard 页面自动刷新数据的时间间隔。设为 0 可以完全禁用自动刷新（需要手动刷新页面）。如果你在编辑内容时被自动刷新打断，可以增大此值或设为 0。",
        "type": "number",
        "min": 0,
        "max": 60000,
        "group": "Dashboard",
    },
    # Prompt 语言配置
    "prompt_language": {
        "label": "Prompt 语言",
        "description": "LLM prompt 使用的语言",
        "tooltip": "控制系统生成 Summary 和 Knowledge 时使用的 prompt 语言。zh=中文，en=英文。修改后立即生效，无需重启服务。",
        "type": "select",
        "options": [
            {"value": "zh", "label": "中文"},
            {"value": "en", "label": "English"},
        ],
        "group": "Prompts",
    },
}


class ConfigManager:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str) -> str:
        """获取配置值，如果不存在则返回默认值"""
        value = self.db.get_config(key)
        if value is None:
            return DEFAULT_CONFIG.get(key, "")
        return value

    def get_int(self, key: str) -> int:
        """获取整数配置"""
        return int(self.get(key))

    def set(self, key: str, value: str):
        """设置配置值"""
        self.db.set_config(key, value)

    def get_all(self) -> dict[str, str]:
        """获取所有配置（合并默认值）"""
        config = DEFAULT_CONFIG.copy()
        with self.db._connect() as conn:
            rows = conn.execute("SELECT key, value FROM config").fetchall()
            for row in rows:
                config[row[0]] = row[1]
        return config

    def get_memory_manager_kwargs(self) -> dict[str, Any]:
        """获取 MemoryManager 初始化参数"""
        return {
            "llm_provider": self.get("llm_provider"),
            "ollama_model": self.get("ollama_model"),
            "ollama_base_url": self.get("ollama_base_url"),
            "ollama_timeout": float(self.get("ollama_timeout")),
            "ollama_keep_alive": self.get("ollama_keep_alive"),
            "anthropic_model": self.get("anthropic_model"),
            "short_term_window_size": self.get_int("short_term_window_size"),
            "max_context_tokens": self.get_int("max_context_tokens"),
            "summary_trigger_threshold": self.get_int("summary_trigger_threshold"),
            "embedding_model": self.get("embedding_model"),
            "embedding_base_url": self.get("embedding_base_url"),  # 空=使用 ollama_base_url
            "enable_vector_search": self.get("enable_vector_search").lower() == "true",
            "enable_knowledge_extraction": self.get("enable_knowledge_extraction").lower() == "true",
            # 总结配置
            "summary_max_chars_total": self.get_int("summary_max_chars_total"),
            "summary_max_chars_per_message": self.get_int("summary_max_chars_per_message"),
            # 知识提取配置
            "knowledge_max_chars_per_message": self.get_int("knowledge_max_chars_per_message"),
            # Prompt 模板
            "summary_prompt_template": self.get("summary_prompt_template"),
            "knowledge_extraction_prompt": self.get("knowledge_extraction_prompt"),
            "knowledge_condense_prompt": self.get("knowledge_condense_prompt"),
        }


def load_config(db_path: Path | str) -> ConfigManager:
    """加载配置管理器"""
    db = Database(db_path)
    return ConfigManager(db)
