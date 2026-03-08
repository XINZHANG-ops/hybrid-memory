from loguru import logger
from .database import Database
from .models import Message

try:
    from rapidfuzz import fuzz
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False


class MemoryRetriever:
    def __init__(self, db: Database):
        self.db = db
        logger.info(f"MemoryRetriever initialized (fuzzy_search={'enabled' if FUZZY_AVAILABLE else 'disabled'})")

    def search(self, query: str, session_id: str | None = None, limit: int = 20, fuzzy: bool = False, threshold: int = 60) -> list[Message]:
        logger.debug(f"MemoryRetriever.search: query='{query}', session={session_id}, limit={limit}, fuzzy={fuzzy}")
        if fuzzy and FUZZY_AVAILABLE:
            return self._fuzzy_search(query, session_id, limit, threshold)
        results = self.db.search_messages(query, session_id)
        limited = results[:limit]
        logger.info(f"Search completed: found {len(results)} results, returning {len(limited)}")
        return limited

    def _fuzzy_search(self, query: str, session_id: str | None, limit: int, threshold: int) -> list[Message]:
        logger.debug(f"Performing fuzzy search with threshold={threshold}")
        all_messages = self.db.get_all_messages_for_search(session_id)
        scored = []
        for msg in all_messages:
            score = fuzz.partial_ratio(query.lower(), msg.content.lower())
            if score >= threshold:
                scored.append((score, msg))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [msg for _, msg in scored[:limit]]
        logger.info(f"Fuzzy search completed: {len(results)} results above threshold {threshold}")
        return results

    def get_all_messages(self, session_id: str, include_summarized: bool = True) -> list[Message]:
        logger.debug(f"MemoryRetriever.get_all_messages: session={session_id}, include_summarized={include_summarized}")
        messages = self.db.get_messages(session_id, include_summarized=include_summarized)
        logger.debug(f"Retrieved {len(messages)} messages")
        return messages
