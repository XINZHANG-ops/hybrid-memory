import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from src.memory_core import MemoryManager

DB_PATH = os.environ.get("HYBRID_MEMORY_DB", None)

server = Server("hybrid-memory")
_manager: MemoryManager | None = None


def get_manager() -> MemoryManager:
    global _manager
    if _manager is None:
        logger.info("Creating MemoryManager instance for MCP server")
        _manager = MemoryManager(db_path=DB_PATH)
    return _manager


@server.list_tools()
async def list_tools():
    logger.debug("MCP list_tools called")
    tools = [
        Tool(
            name="memory_add",
            description="Add a message to memory",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session identifier"},
                    "role": {"type": "string", "enum": ["user", "assistant", "system"]},
                    "content": {"type": "string", "description": "Message content"},
                },
                "required": ["session_id", "role", "content"],
            },
        ),
        Tool(
            name="memory_get_context",
            description="Get conversation context (summaries + recent messages)",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "max_tokens": {"type": "integer", "description": "Max tokens for context"},
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="memory_search",
            description="Search memory for relevant messages",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "session_id": {"type": "string", "description": "Optional: limit to session"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="memory_trigger_summary",
            description="Manually trigger summarization",
            inputSchema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        ),
        Tool(
            name="memory_end_session",
            description="End a session and create final summary",
            inputSchema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        ),
    ]
    logger.debug(f"Returning {len(tools)} tools")
    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    logger.info(f"MCP call_tool: name={name}, arguments={arguments}")
    manager = get_manager()

    if name == "memory_add":
        logger.debug(f"Adding message: session={arguments['session_id']}, role={arguments['role']}")
        msg = manager.add_message(
            arguments["session_id"], arguments["role"], arguments["content"]
        )
        logger.info(f"Message added via MCP: id={msg.id}")
        return [TextContent(type="text", text=f"Message added (id={msg.id})")]

    elif name == "memory_get_context":
        logger.debug(f"Getting context: session={arguments['session_id']}")
        ctx = manager.get_context(
            arguments["session_id"], arguments.get("max_tokens")
        )
        import json
        result = json.dumps(ctx, ensure_ascii=False, indent=2)
        logger.info(f"Context retrieved via MCP: {len(result)} chars")
        return [TextContent(type="text", text=result)]

    elif name == "memory_search":
        logger.debug(f"Searching memory: query='{arguments['query']}'")
        results = manager.search_memory(
            arguments["query"], arguments.get("session_id")
        )
        lines = [f"[{m.session_id}] {m.role}: {m.content[:200]}" for m in results[:10]]
        logger.info(f"Search completed via MCP: {len(results)} results")
        return [TextContent(type="text", text="\n".join(lines) or "No results found")]

    elif name == "memory_trigger_summary":
        logger.debug(f"Triggering summary: session={arguments['session_id']}")
        summary = manager.trigger_summary(arguments["session_id"])
        if summary:
            logger.info(f"Summary created via MCP: id={summary.id}")
            return [TextContent(type="text", text=f"Summary created:\n{summary.summary_text}")]
        logger.debug("No messages to summarize")
        return [TextContent(type="text", text="No messages to summarize")]

    elif name == "memory_end_session":
        logger.debug(f"Ending session: {arguments['session_id']}")
        summary = manager.end_session(arguments["session_id"])
        if summary:
            logger.info(f"Session ended via MCP with summary: id={summary.id}")
            return [TextContent(type="text", text=f"Session ended. Summary:\n{summary.summary_text}")]
        logger.info("Session ended via MCP without summary")
        return [TextContent(type="text", text="Session ended (no summary needed)")]

    logger.warning(f"Unknown tool called: {name}")
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    logger.info("Starting MCP server: hybrid-memory")
    async with stdio_server() as (read_stream, write_stream):
        logger.debug("MCP server running with stdio transport")
        await server.run(read_stream, write_stream, server.create_initialization_options())
    logger.info("MCP server stopped")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
