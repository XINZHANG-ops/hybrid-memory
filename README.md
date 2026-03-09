# Hybrid Memory System

Claude Code 的记忆增强系统，通过 hooks 自动保存对话历史、提取知识、生成摘要。

## 功能

- **短期记忆**: 保存对话消息到 SQLite 数据库
- **长期记忆**: 自动生成对话摘要
- **知识提取**: 从对话中提取用户偏好、项目决策等结构化知识
- **向量搜索**: 基于语义相似度检索历史消息
- **Token 追踪**: 统计每个项目的 token 使用量和费用
- **Dashboard UI**: Web 界面查看和管理记忆数据

## 快速开始

### 安装

```bash
cd D:\python\hybrid-memory
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 配置 Claude Code Hooks

在 `~/.claude/settings.json` 中添加 hooks 配置（参考 `.claude/hooks/` 目录）。

## Dashboard

Dashboard 会在 Claude Code 启动时自动运行（通过 SessionStart hook）。

访问地址: http://localhost:37888

### 重要: 更新代码后重启 Dashboard

当你修改了 Dashboard 或 API 代码后，需要手动重启才能看到变化：

**Windows:**
```bash
# 杀掉 Dashboard 进程
taskkill /F /IM python.exe

# 重启 Claude Code，Dashboard 会自动启动
```

**macOS/Linux:**
```bash
# 方法1: 杀掉占用 37888 端口的进程
lsof -ti :37888 | xargs kill -9

# 方法2: 杀掉所有 Python 进程（慎用）
pkill -f python

# 重启 Claude Code，Dashboard 会自动启动
```

## 目录结构

```
hybrid-memory/
├── .claude/hooks/       # Claude Code hooks
│   ├── sessionStart.py  # 启动时注入上下文
│   ├── userPromptSubmit.py  # 用户提交时保存消息
│   └── stop.py          # 结束时保存助手回复和 token 统计
├── src/
│   ├── memory_core/     # 核心记忆模块
│   │   ├── database.py  # SQLite 数据库操作
│   │   ├── manager.py   # 记忆管理器
│   │   ├── summarizer.py    # 摘要生成
│   │   └── knowledge_extractor.py  # 知识提取
│   └── http_api/        # Web API 和 Dashboard
│       ├── app.py       # FastAPI 路由
│       └── dashboard.py # Dashboard UI
└── data/                # 数据存储
    ├── global_memory.db # 全局记忆数据库
    └── projects/        # 各项目独立数据库
```

## 配置

Dashboard 的 Config 页面可以配置：
- LLM 提供商和模型
- Token 价格（用于费用统计）
- 摘要和知识提取的触发阈值
- Prompt 模板

## Token 统计说明

### 数据来源

Token 使用数据从 Claude Code 的 transcript 文件中提取（stop.py hook）。

### Input Tokens 计算

Claude API 的 usage 包含多个字段：
- `input_tokens`: 不包含缓存的新输入 token
- `cache_creation_input_tokens`: 用于创建缓存的 token
- `cache_read_input_tokens`: 从缓存读取的 token

**完整 Input = input_tokens + cache_creation_input_tokens + cache_read_input_tokens**

注意：三种 token 都需要计费，只是价格不同（cache 读取最便宜）。

### Output Tokens 估算

由于 Claude Code 的 transcript 格式限制（流式响应中每条消息的 `output_tokens` 只记录增量标记=1），无法直接获取真实的 output tokens。

**解决方案**：根据 assistant 回复内容长度估算 token 数：
- 包含：thinking（思考）、text（文本回复）、tool_use（工具调用）
- 估算规则：1 token ≈ 4 英文字符 或 1.5 中文字符

这是估算值，不是精确值，但比 transcript 记录的全是 1 准确得多。

### 历史数据

2026-03-09 之前的历史数据存在以下问题：
- Input tokens 未计入 cache 相关 token（数值偏小）
- Output tokens 被流式消息重复累加（数值偏大）

历史数据未做追溯修正，保持原样。

### 准确性

- **Input tokens**: 准确（包含 cache tokens）
- **Output tokens**: 估算值（基于内容长度）
- **费用**: 大致准确（主要由 Input 决定，且 cache 读取价格未细分）
