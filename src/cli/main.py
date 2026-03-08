import click
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger
from src.memory_core import MemoryManager


def get_manager(db_path: str | None = None) -> MemoryManager:
    logger.debug(f"Creating MemoryManager for CLI: db_path={db_path}")
    return MemoryManager(db_path=db_path)


@click.group()
@click.option("--db", default=None, help="Database path")
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.pass_context
def cli(ctx, db, debug):
    ctx.ensure_object(dict)
    ctx.obj["db"] = db
    if debug:
        logger.enable("")
        logger.add(sys.stderr, level="DEBUG")
        logger.debug("Debug logging enabled")
    else:
        logger.disable("")


@cli.command()
@click.argument("session_id")
@click.pass_context
def start(ctx, session_id):
    """Start a new session."""
    logger.info(f"CLI start: session={session_id}")
    manager = get_manager(ctx.obj["db"])
    manager.start_session(session_id)
    click.echo(f"Session '{session_id}' started.")


@cli.command()
@click.argument("session_id")
@click.argument("role", type=click.Choice(["user", "assistant", "system"]))
@click.argument("content")
@click.pass_context
def add(ctx, session_id, role, content):
    """Add a message to a session."""
    logger.info(f"CLI add: session={session_id}, role={role}")
    logger.debug(f"Content: {content[:100]}...")
    manager = get_manager(ctx.obj["db"])
    msg = manager.add_message(session_id, role, content)
    logger.info(f"Message added: id={msg.id}")
    click.echo(f"Message added (id={msg.id}).")


@cli.command()
@click.argument("session_id")
@click.option("--max-tokens", default=None, type=int, help="Maximum tokens")
@click.pass_context
def context(ctx, session_id, max_tokens):
    """Get context for a session."""
    logger.info(f"CLI context: session={session_id}, max_tokens={max_tokens}")
    manager = get_manager(ctx.obj["db"])
    result = manager.get_context(session_id, max_tokens)
    logger.debug(f"Context: summaries_len={len(result['summaries'])}, messages_count={len(result['messages'])}")
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@cli.command()
@click.argument("query")
@click.option("--session", default=None, help="Limit to session")
@click.pass_context
def search(ctx, query, session):
    """Search memory."""
    logger.info(f"CLI search: query='{query}', session={session}")
    manager = get_manager(ctx.obj["db"])
    results = manager.search_memory(query, session)
    logger.info(f"Search found {len(results)} results")
    for msg in results:
        click.echo(f"[{msg.session_id}] {msg.role}: {msg.content[:100]}...")


@cli.command()
@click.argument("session_id")
@click.pass_context
def summarize(ctx, session_id):
    """Trigger summary for a session."""
    logger.info(f"CLI summarize: session={session_id}")
    manager = get_manager(ctx.obj["db"])
    summary = manager.trigger_summary(session_id)
    if summary:
        logger.info(f"Summary created: id={summary.id}")
        click.echo(f"Summary created:\n{summary.summary_text}")
    else:
        logger.debug("No messages to summarize")
        click.echo("No messages to summarize.")


@cli.command()
@click.argument("session_id")
@click.pass_context
def end(ctx, session_id):
    """End a session and create final summary."""
    logger.info(f"CLI end: session={session_id}")
    manager = get_manager(ctx.obj["db"])
    summary = manager.end_session(session_id)
    if summary:
        logger.info(f"Session ended with summary: id={summary.id}")
        click.echo(f"Session ended. Summary:\n{summary.summary_text}")
    else:
        logger.info("Session ended without summary")
        click.echo("Session ended (no summary needed).")


@cli.command()
@click.argument("session_id")
@click.pass_context
def status(ctx, session_id):
    """Show session status."""
    logger.info(f"CLI status: session={session_id}")
    manager = get_manager(ctx.obj["db"])
    session = manager.get_session(session_id)
    if session:
        logger.debug(f"Session found: active={session.is_active}")
        click.echo(f"Session: {session.session_id}")
        click.echo(f"Active: {session.is_active}")
        click.echo(f"Started: {session.started_at}")
        click.echo(f"Last active: {session.last_active_at}")
        count = manager.short_term.count_unsummarized(session_id)
        click.echo(f"Unsummarized messages: {count}")
    else:
        logger.warning(f"Session not found: {session_id}")
        click.echo("Session not found.")


if __name__ == "__main__":
    cli()
