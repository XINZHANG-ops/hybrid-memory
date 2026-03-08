from loguru import logger
from .database import Database
from .models import Message


class ShortTermMemory:
    def __init__(self, db: Database, window_size: int = 20, max_tokens: int = 8000):
        self.db = db
        self.window_size = window_size
        self.max_tokens = max_tokens
        logger.info(f"ShortTermMemory initialized: window_size={window_size}, max_tokens={max_tokens}")

    def add(self, session_id: str, role: str, content: str, token_count: int = 0) -> Message:
        estimated_tokens = token_count or self._estimate_tokens(content)
        logger.debug(f"ShortTermMemory.add: session={session_id}, role={role}, tokens={estimated_tokens}")
        message = Message(
            id=None,
            session_id=session_id,
            role=role,
            content=content,
            token_count=estimated_tokens,
        )
        saved = self.db.add_message(message)
        logger.debug(f"Message saved to short-term memory: id={saved.id}")
        return saved

    def get_recent(self, session_id: str) -> list[Message]:
        logger.debug(f"ShortTermMemory.get_recent: session={session_id}, window_size={self.window_size}")
        messages = self.db.get_unsummarized_messages(session_id)
        if len(messages) <= self.window_size:
            logger.debug(f"Returning all {len(messages)} unsummarized messages")
            return messages
        result = messages[-self.window_size:]
        logger.debug(f"Returning last {len(result)} of {len(messages)} messages (window limit)")
        return result

    def get_within_token_limit(self, session_id: str, max_tokens: int | None = None) -> list[Message]:
        limit = max_tokens or self.max_tokens
        logger.debug(f"ShortTermMemory.get_within_token_limit: session={session_id}, max_tokens={limit}")
        messages = self.db.get_unsummarized_messages(session_id)
        result = []
        total_tokens = 0
        for msg in reversed(messages):
            if total_tokens + msg.token_count > limit:
                logger.debug(f"Token limit reached: {total_tokens} + {msg.token_count} > {limit}")
                break
            result.insert(0, msg)
            total_tokens += msg.token_count
        logger.debug(f"Returning {len(result)} messages within {total_tokens} tokens")
        return result

    def count_unsummarized(self, session_id: str) -> int:
        count = self.db.count_unsummarized_messages(session_id)
        logger.debug(f"ShortTermMemory.count_unsummarized: session={session_id}, count={count}")
        return count

    def _estimate_tokens(self, text: str) -> int:
        estimated = len(text) // 4 + 1
        logger.debug(f"Estimated tokens for text (len={len(text)}): {estimated}")
        return estimated
