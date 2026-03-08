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
    "ollama_model": "qwen2.5:7b",
    "ollama_base_url": "http://localhost:11434",
    "ollama_timeout": "300",
    "ollama_keep_alive": "10m",
    "anthropic_model": "claude-sonnet-4-20250514",
    "embedding_model": "embeddinggemma:300m",
    "enable_vector_search": "true",
    "enable_knowledge_extraction": "true",
}

# 配置元数据（用于 UI 展示）
CONFIG_META = {
    "short_term_window_size": {
        "label": "短期记忆消息数",
        "description": "保留最近多少条消息作为短期记忆",
        "type": "number",
        "min": 5,
        "max": 100,
    },
    "max_context_tokens": {
        "label": "最大上下文 Token",
        "description": "注入上下文时的最大 token 数",
        "type": "number",
        "min": 1000,
        "max": 32000,
    },
    "summary_trigger_threshold": {
        "label": "总结触发阈值",
        "description": "未总结消息达到多少条时自动生成摘要",
        "type": "number",
        "min": 10,
        "max": 200,
    },
    "llm_provider": {
        "label": "LLM 提供者",
        "description": "用于生成摘要的 LLM",
        "type": "select",
        "options": ["ollama", "anthropic"],
    },
    "ollama_model": {
        "label": "Ollama 模型",
        "description": "Ollama 使用的模型名称",
        "type": "text",
    },
    "ollama_base_url": {
        "label": "Ollama 地址",
        "description": "Ollama 服务地址",
        "type": "text",
    },
    "ollama_timeout": {
        "label": "Ollama 超时(秒)",
        "description": "API 请求超时时间，大模型需要更长时间",
        "type": "number",
        "min": 60,
        "max": 600,
    },
    "ollama_keep_alive": {
        "label": "Ollama Keep Alive",
        "description": "模型在内存中保持时间，如 5m, 10m, 1h, -1(永不卸载)",
        "type": "text",
    },
    "anthropic_model": {
        "label": "Anthropic 模型",
        "description": "Anthropic 使用的模型",
        "type": "text",
    },
    "embedding_model": {
        "label": "Embedding 模型",
        "description": "用于向量化的 Ollama embedding 模型",
        "type": "text",
    },
    "enable_vector_search": {
        "label": "启用向量搜索",
        "description": "使用 embedding 进行语义相似度搜索",
        "type": "select",
        "options": ["true", "false"],
    },
    "enable_knowledge_extraction": {
        "label": "启用知识提取",
        "description": "自动从对话中提取结构化知识",
        "type": "select",
        "options": ["true", "false"],
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
            "enable_vector_search": self.get("enable_vector_search").lower() == "true",
            "enable_knowledge_extraction": self.get("enable_knowledge_extraction").lower() == "true",
        }


def load_config(db_path: Path | str) -> ConfigManager:
    """加载配置管理器"""
    db = Database(db_path)
    return ConfigManager(db)
