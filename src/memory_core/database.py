import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
from loguru import logger
from .models import Message, Summary, Session, TokenUsage

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "memory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    session_id TEXT UNIQUE NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT 1
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    token_count INTEGER DEFAULT 0,
    is_summarized BOOLEAN DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    message_range_start INTEGER,
    message_range_end INTEGER,
    message_count INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, category, content)
);

CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    model TEXT DEFAULT '',
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_session ON knowledge(session_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category);
CREATE INDEX IF NOT EXISTS idx_messages_summarized ON messages(is_summarized);
CREATE INDEX IF NOT EXISTS idx_summaries_session ON summaries(session_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_session ON token_usage(session_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp ON token_usage(timestamp);
"""


class Database:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        logger.debug(f"Initializing database at: {self.db_path}")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        logger.info(f"Database initialized: {self.db_path}")

    def _init_schema(self):
        logger.debug("Creating database schema if not exists")
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        logger.debug("Schema initialization complete")

    @contextmanager
    def _connect(self):
        logger.debug(f"Opening database connection: {self.db_path}")
        conn = sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
            logger.debug("Database transaction committed")
        finally:
            conn.close()
            logger.debug("Database connection closed")

    def create_session(self, session_id: str) -> Session:
        logger.debug(f"Creating session: {session_id}")
        with self._connect() as conn:
            now = datetime.now()
            conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, started_at, last_active_at) VALUES (?, ?, ?)",
                (session_id, now, now),
            )
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            session = self._row_to_session(row)
            logger.info(f"Session created/retrieved: {session_id} (id={session.id})")
            return session

    def get_session(self, session_id: str) -> Session | None:
        logger.debug(f"Getting session: {session_id}")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row:
                session = self._row_to_session(row)
                logger.debug(f"Session found: {session_id} (active={session.is_active})")
                return session
            logger.debug(f"Session not found: {session_id}")
            return None

    def update_session_activity(self, session_id: str):
        logger.debug(f"Updating session activity: {session_id}")
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET last_active_at = ? WHERE session_id = ?",
                (datetime.now(), session_id),
            )
        logger.debug(f"Session activity updated: {session_id}")

    def end_session(self, session_id: str):
        logger.debug(f"Ending session: {session_id}")
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET is_active = 0 WHERE session_id = ?", (session_id,)
            )
        logger.info(f"Session ended: {session_id}")

    def add_message(self, message: Message) -> Message:
        logger.debug(f"Adding message: session={message.session_id}, role={message.role}, tokens={message.token_count}")
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO messages (session_id, role, content, timestamp, token_count, is_summarized)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    message.session_id,
                    message.role,
                    message.content,
                    message.timestamp,
                    message.token_count,
                    message.is_summarized,
                ),
            )
            message.id = cursor.lastrowid
            logger.info(f"Message added: id={message.id}, session={message.session_id}, role={message.role}")
            logger.debug(f"Message content preview: {message.content[:100]}...")
            return message

    def get_messages(
        self, session_id: str, include_summarized: bool = False, limit: int | None = None
    ) -> list[Message]:
        logger.debug(f"Getting messages: session={session_id}, include_summarized={include_summarized}, limit={limit}")
        with self._connect() as conn:
            query = "SELECT * FROM messages WHERE session_id = ?"
            params: list = [session_id]
            if not include_summarized:
                query += " AND is_summarized = 0"
            query += " ORDER BY id ASC"
            if limit:
                query += " LIMIT ?"
                params.append(limit)
            rows = conn.execute(query, params).fetchall()
            messages = [self._row_to_message(row) for row in rows]
            logger.debug(f"Retrieved {len(messages)} messages for session {session_id}")
            return messages

    def get_unsummarized_messages(self, session_id: str | None = None) -> list[Message]:
        """获取未总结消息。session_id=None 时获取所有 session 的未总结消息"""
        logger.debug(f"Getting unsummarized messages: session={session_id}")
        with self._connect() as conn:
            if session_id is None:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE is_summarized = 0 ORDER BY id ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? AND is_summarized = 0 ORDER BY id ASC",
                    (session_id,),
                ).fetchall()
            messages = [self._row_to_message(row) for row in rows]
            logger.debug(f"Retrieved {len(messages)} unsummarized messages for {session_id or 'ALL'}")
            return messages

    def mark_messages_summarized(self, message_ids: list[int]):
        if not message_ids:
            logger.debug("No message IDs to mark as summarized")
            return
        logger.debug(f"Marking {len(message_ids)} messages as summarized: {message_ids}")
        with self._connect() as conn:
            placeholders = ",".join("?" * len(message_ids))
            conn.execute(
                f"UPDATE messages SET is_summarized = 1 WHERE id IN ({placeholders})",
                message_ids,
            )
        logger.info(f"Marked {len(message_ids)} messages as summarized")

    def count_unsummarized_messages(self, session_id: str | None = None) -> int:
        """计数未总结消息。session_id=None 时统计所有 session"""
        logger.debug(f"Counting unsummarized messages: session={session_id}")
        with self._connect() as conn:
            if session_id is None:
                row = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE is_summarized = 0"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ? AND is_summarized = 0",
                    (session_id,),
                ).fetchone()
            count = row[0]
            logger.debug(f"Unsummarized message count for {session_id or 'ALL'}: {count}")
            return count

    def add_summary(self, summary: Summary) -> Summary:
        logger.debug(f"Adding summary: session={summary.session_id}, message_count={summary.message_count}")
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO summaries (session_id, summary_text, message_range_start, message_range_end, message_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    summary.session_id,
                    summary.summary_text,
                    summary.message_range_start,
                    summary.message_range_end,
                    summary.message_count,
                    summary.created_at,
                ),
            )
            summary.id = cursor.lastrowid
            logger.info(f"Summary added: id={summary.id}, session={summary.session_id}, messages={summary.message_count}")
            logger.debug(f"Summary preview: {summary.summary_text[:200]}...")
            return summary

    def get_summaries(self, session_id: str) -> list[Summary]:
        logger.debug(f"Getting summaries: session={session_id}")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM summaries WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            summaries = [self._row_to_summary(row) for row in rows]
            logger.debug(f"Retrieved {len(summaries)} summaries for session {session_id}")
            return summaries

    def get_all_summaries(self, limit: int = 10) -> list[Summary]:
        """获取所有会话的摘要（按时间倒序）"""
        logger.debug(f"Getting all summaries: limit={limit}")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM summaries ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            summaries = [self._row_to_summary(row) for row in rows]
            logger.debug(f"Retrieved {len(summaries)} summaries across all sessions")
            return summaries

    def get_recent_messages_all_sessions(self, limit: int = 20) -> list[Message]:
        """获取所有会话的最近消息"""
        logger.debug(f"Getting recent messages from all sessions: limit={limit}")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            messages = [self._row_to_message(row) for row in rows]
            logger.debug(f"Retrieved {len(messages)} recent messages across all sessions")
            return messages

    def get_config(self, key: str, default: str | None = None) -> str | None:
        logger.debug(f"Getting config: key={key}")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM config WHERE key = ?", (key,)
            ).fetchone()
            value = row[0] if row else default
            logger.debug(f"Config value for {key}: {value}")
            return value

    def set_config(self, key: str, value: str):
        logger.debug(f"Setting config: key={key}, value={value}")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
            )
        logger.info(f"Config set: {key}={value}")

    def search_messages(self, query: str, session_id: str | None = None) -> list[Message]:
        logger.debug(f"Searching messages: query='{query}', session={session_id}")
        with self._connect() as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? AND content LIKE ? ORDER BY id DESC",
                    (session_id, f"%{query}%"),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE content LIKE ? ORDER BY id DESC",
                    (f"%{query}%",),
                ).fetchall()
            messages = [self._row_to_message(row) for row in rows]
            logger.info(f"Search found {len(messages)} messages for query '{query}'")
            return messages

    def get_all_messages_for_search(self, session_id: str | None = None) -> list[Message]:
        logger.debug(f"Getting all messages for fuzzy search: session={session_id}")
        with self._connect() as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? ORDER BY id DESC",
                    (session_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages ORDER BY id DESC"
                ).fetchall()
            messages = [self._row_to_message(row) for row in rows]
            logger.debug(f"Retrieved {len(messages)} messages for search")
            return messages

    def _row_to_message(self, row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            timestamp=row["timestamp"] if isinstance(row["timestamp"], datetime) else datetime.fromisoformat(row["timestamp"]),
            token_count=row["token_count"],
            is_summarized=bool(row["is_summarized"]),
        )

    def _row_to_summary(self, row: sqlite3.Row) -> Summary:
        return Summary(
            id=row["id"],
            session_id=row["session_id"],
            summary_text=row["summary_text"],
            message_range_start=row["message_range_start"],
            message_range_end=row["message_range_end"],
            message_count=row["message_count"],
            created_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(row["created_at"]),
        )

    def _row_to_session(self, row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            session_id=row["session_id"],
            started_at=row["started_at"] if isinstance(row["started_at"], datetime) else datetime.fromisoformat(row["started_at"]),
            last_active_at=row["last_active_at"] if isinstance(row["last_active_at"], datetime) else datetime.fromisoformat(row["last_active_at"]),
            is_active=bool(row["is_active"]),
        )

    def get_message_by_id(self, message_id: int) -> Message | None:
        logger.debug(f"Getting message by id: {message_id}")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            if row:
                return self._row_to_message(row)
            return None

    def get_summary_by_id(self, summary_id: int) -> Summary | None:
        logger.debug(f"Getting summary by id: {summary_id}")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM summaries WHERE id = ?", (summary_id,)
            ).fetchone()
            if row:
                return self._row_to_summary(row)
            return None

    def update_summary_text(self, summary_id: int, text: str):
        logger.debug(f"Updating summary text: id={summary_id}")
        with self._connect() as conn:
            conn.execute(
                "UPDATE summaries SET summary_text = ? WHERE id = ?",
                (text, summary_id)
            )
        logger.info(f"Summary updated: id={summary_id}")

    def get_messages_in_range(self, start_id: int, end_id: int) -> list[Message]:
        logger.debug(f"Getting messages in range: {start_id} to {end_id}")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE id >= ? AND id <= ? ORDER BY id ASC",
                (start_id, end_id)
            ).fetchall()
            messages = [self._row_to_message(row) for row in rows]
            logger.debug(f"Retrieved {len(messages)} messages in range")
            return messages

    def save_knowledge(self, session_id: str | None, knowledge: dict):
        logger.debug(f"Saving knowledge for session: {session_id}")
        with self._connect() as conn:
            for category, items in knowledge.items():
                for item in items:
                    if item:
                        conn.execute(
                            "INSERT OR IGNORE INTO knowledge (session_id, category, content) VALUES (?, ?, ?)",
                            (session_id, category, item)
                        )
        logger.info(f"Knowledge saved for session {session_id}")

    def get_knowledge(self, session_id: str | None = None) -> dict:
        logger.debug(f"Getting knowledge for session: {session_id}")
        categories = ["user_preferences", "project_decisions", "key_facts",
                     "pending_tasks", "learned_patterns", "important_context"]
        result = {c: [] for c in categories}

        with self._connect() as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT category, content FROM knowledge WHERE session_id = ? OR session_id IS NULL",
                    (session_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT category, content FROM knowledge"
                ).fetchall()

            for row in rows:
                cat = row["category"]
                if cat in result:
                    result[cat].append(row["content"])

        total = sum(len(v) for v in result.values())
        logger.debug(f"Retrieved {total} knowledge items")
        return result

    def add_token_usage(self, usage: TokenUsage) -> TokenUsage:
        logger.debug(f"Adding token usage: session={usage.session_id}, input={usage.input_tokens}, output={usage.output_tokens}")
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO token_usage (session_id, input_tokens, output_tokens, model, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (usage.session_id, usage.input_tokens, usage.output_tokens, usage.model, usage.timestamp),
            )
            usage.id = cursor.lastrowid
            logger.info(f"Token usage added: id={usage.id}, input={usage.input_tokens}, output={usage.output_tokens}")
            return usage

    def get_token_usage_stats(self, session_id: str | None = None) -> dict:
        logger.debug(f"Getting token usage stats: session={session_id}")
        with self._connect() as conn:
            if session_id:
                row = conn.execute(
                    """SELECT SUM(input_tokens) as total_input, SUM(output_tokens) as total_output, COUNT(*) as count
                       FROM token_usage WHERE session_id = ?""",
                    (session_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT SUM(input_tokens) as total_input, SUM(output_tokens) as total_output, COUNT(*) as count
                       FROM token_usage"""
                ).fetchone()
            stats = {
                "total_input_tokens": row["total_input"] or 0,
                "total_output_tokens": row["total_output"] or 0,
                "request_count": row["count"] or 0,
            }
            logger.debug(f"Token stats: {stats}")
            return stats

    def get_token_usage_history(self, session_id: str | None = None, limit: int = 100) -> list[TokenUsage]:
        logger.debug(f"Getting token usage history: session={session_id}, limit={limit}")
        with self._connect() as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM token_usage WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM token_usage ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            usages = [self._row_to_token_usage(row) for row in rows]
            logger.debug(f"Retrieved {len(usages)} token usage records")
            return usages

    def _row_to_token_usage(self, row: sqlite3.Row) -> TokenUsage:
        return TokenUsage(
            id=row["id"],
            session_id=row["session_id"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            model=row["model"] or "",
            timestamp=row["timestamp"] if isinstance(row["timestamp"], datetime) else datetime.fromisoformat(row["timestamp"]),
        )
