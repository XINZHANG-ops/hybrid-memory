# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Hybrid Memory is a memory enhancement system for Claude Code. It persists conversation history, extracts structured knowledge, generates summaries, and provides semantic search via Claude Code hooks integration.

**Dual-Layer Architecture**: Project-specific memory (`data/projects/{name}.db`) + global memory (`data/global_memory.db`) for cross-project context sharing.

## Commands

```bash
# Install
python install.py

# Run dashboard manually (auto-starts via SessionStart hook)
.venv/Scripts/python src/http_api/dashboard.py      # Windows
.venv/bin/python src/http_api/dashboard.py          # Mac/Linux

# Restart dashboard after code changes
taskkill /F /IM python.exe                          # Windows
lsof -ti :37888 | xargs kill -9                     # Mac/Linux
# Then restart Claude Code

# Clean empty sessions
.venv/Scripts/python -c "from src.memory_core.database import Database; from pathlib import Path; [print(f'Cleaned {Database(p).delete_empty_sessions()} from {p.name}') for p in Path('data/projects').glob('*.db')]"
```

Dashboard: http://localhost:37888

## Architecture

### Hook Flow
```
SessionStart → Load context (summaries/knowledge/decisions) → Inject to Claude
     ↓
UserPromptSubmit → Save user message to project + global DB
     ↓
Stop → Save assistant response → Launch background_summary.py
     ↓
background_summary.py (async) → Embeddings → Summarize → Extract knowledge/decisions
```

### Core Modules (`src/memory_core/`)

| Module | Purpose |
|--------|---------|
| `manager.py` | Main orchestrator: sessions, messages, context retrieval |
| `database.py` | SQLite operations, schema migrations |
| `summarizer.py` | LLM-based summarization with content filtering |
| `knowledge_extractor.py` | Extract 6 categories: user_preferences, project_decisions, key_facts, pending_tasks, learned_patterns, important_context |
| `decision_extractor.py` | Extract problem/solution decisions with pending→confirmed workflow |
| `vector_store.py` | FAISS semantic search |
| `embedding_client.py` | Ollama embedding API |
| `llm_client.py` | LLM abstraction (Ollama/Anthropic) with retry logic |
| `config.py` | Global configuration from database |
| `prompts.py` | LLM prompt templates |
| `content_processor.py` | ContentConfig for filtering thinking/tool/text blocks |
| `hook_utils.py` | Shared utilities for hooks: paths, logging, UTF-8, text sanitization |

### Hooks (`.claude/hooks/`)

| Hook | Trigger | Key Actions |
|------|---------|-------------|
| `sessionStart.py` | Session begins | Start dashboard, inject context |
| `userPromptSubmit.py` | User sends message | Save to both DBs |
| `stop.py` | Turn ends | Save response, track tokens, launch background process |
| `background_summary.py` | Async from stop | Embeddings, summarization, knowledge/decision extraction |
| `permissionRequest.py` | Permission asked | Track pending permissions |
| `postToolUse.py` | Tool executed | Track tool usage |

### Web API (`src/http_api/`)

FastAPI on port 37888. Key endpoints:
- `GET /api/projects` - List projects with stats
- `GET /api/projects/{name}/messages` - Get messages
- `GET /api/projects/{name}/context` - Preview injected context
- `GET /api/token-usage` - Token stats
- `GET/POST /api/config` - Configuration

## Key Patterns

**Message Flags**: `is_summarized`, `is_knowledge_extracted`, `is_decision_extracted` - track which messages have been processed.

**Content Filtering**: `ContentConfig` controls inclusion of thinking/tool/text blocks and their truncation limits.

**Token Tracking**: Input tokens are exact (from Claude API), output tokens are estimated (~4 chars/token English, ~1.5 chars/token Chinese).

**Background Processing**: Embeddings, summarization, and extraction run in subprocess to avoid blocking Claude.

**Decision Workflow**: Extract as `pending` → User confirms/rejects in dashboard → Confirmed decisions injected in next session.

## Database Schema (SQLite)

Key tables: `sessions`, `messages`, `summaries`, `knowledge`, `knowledge_history`, `decisions`, `token_usage`, `interactions`, `config`

## Configuration

Managed via dashboard Config tab or `config` table in global DB. Key settings:
- `llm_provider`: "ollama" or "anthropic"
- `summary_trigger_threshold`: Messages before auto-summarize (default: 50)
- `inject_summary_count`, `inject_recent_count`, `inject_knowledge_count`, `inject_decision_count`: Context injection limits
