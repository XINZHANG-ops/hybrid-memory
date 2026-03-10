from loguru import logger
from .database import Database
from .models import Message, Decision

try:
    from rapidfuzz import fuzz
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False

try:
    from rank_bm25 import BM25Okapi
    import jieba
    BM25_AVAILABLE = True
except ImportError:
    BM25_AVAILABLE = False


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

    def bm25_search(self, query: str, session_id: str | None = None, limit: int = 20) -> list[tuple[Message, float]]:
        """使用 BM25 算法搜索消息，返回 (消息, 分数) 列表"""
        if not BM25_AVAILABLE:
            logger.warning("BM25 not available, falling back to fuzzy search")
            results = self._fuzzy_search(query, session_id, limit, threshold=30)
            return [(msg, 0.5) for msg in results]

        logger.debug(f"Performing BM25 search: query='{query}', session={session_id}")
        all_messages = self.db.get_all_messages_for_search(session_id)
        if not all_messages:
            return []

        # 分词：中英文混合分词
        def tokenize(text: str) -> list[str]:
            # 先用 jieba 分词
            tokens = list(jieba.cut(text.lower()))
            # 过滤掉空白和标点
            tokens = [t.strip() for t in tokens if t.strip() and len(t.strip()) > 0]
            return tokens

        # 构建语料库
        corpus = [tokenize(msg.content) for msg in all_messages]
        query_tokens = tokenize(query)

        if not query_tokens or not any(corpus):
            return []

        # 创建 BM25 实例并搜索
        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(query_tokens)

        # 按分数排序
        scored_messages = [(msg, score) for msg, score in zip(all_messages, scores) if score > 0]
        scored_messages.sort(key=lambda x: x[1], reverse=True)

        results = scored_messages[:limit]
        logger.info(f"BM25 search completed: {len(results)} results")
        return results

    def decision_bm25_search(self, query: str, limit: int = 20) -> list[tuple[Decision, float]]:
        """使用 BM25 算法搜索 Decision"""
        if not BM25_AVAILABLE:
            logger.warning("BM25 not available, falling back to fuzzy search")
            return self._decision_fuzzy_search(query, limit, threshold=30)

        logger.debug(f"Performing Decision BM25 search: query='{query}'")
        decisions = self.db.get_decisions(status="confirmed", limit=500)
        if not decisions:
            return []

        def tokenize(text: str) -> list[str]:
            tokens = list(jieba.cut(text.lower()))
            return [t.strip() for t in tokens if t.strip() and len(t.strip()) > 0]

        # 将 problem + solution + reason 组合作为文档
        def get_decision_text(d: Decision) -> str:
            text = f"{d.problem} {d.solution}"
            if d.reason:
                text += f" {d.reason}"
            return text

        corpus = [tokenize(get_decision_text(d)) for d in decisions]
        query_tokens = tokenize(query)

        if not query_tokens or not any(corpus):
            return []

        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(query_tokens)

        scored_decisions = [(d, score) for d, score in zip(decisions, scores) if score > 0]
        scored_decisions.sort(key=lambda x: x[1], reverse=True)

        results = scored_decisions[:limit]
        logger.info(f"Decision BM25 search completed: {len(results)} results")
        return results

    def _decision_fuzzy_search(self, query: str, limit: int, threshold: int = 60) -> list[tuple[Decision, float]]:
        """使用 fuzzy 搜索 Decision"""
        if not FUZZY_AVAILABLE:
            logger.warning("Fuzzy search not available")
            return []

        logger.debug(f"Performing Decision fuzzy search with threshold={threshold}")
        decisions = self.db.get_decisions(status="confirmed", limit=500)

        def get_decision_text(d: Decision) -> str:
            text = f"{d.problem} {d.solution}"
            if d.reason:
                text += f" {d.reason}"
            return text

        scored = []
        for d in decisions:
            text = get_decision_text(d)
            score = fuzz.partial_ratio(query.lower(), text.lower())
            if score >= threshold:
                scored.append((d, score / 100.0))  # 归一化到 0-1

        scored.sort(key=lambda x: x[1], reverse=True)
        results = scored[:limit]
        logger.info(f"Decision fuzzy search completed: {len(results)} results")
        return results

    def decision_fuzzy_search(self, query: str, limit: int = 20, threshold: int = 60) -> list[tuple[Decision, float]]:
        """公开的 Decision fuzzy 搜索方法"""
        return self._decision_fuzzy_search(query, limit, threshold)
