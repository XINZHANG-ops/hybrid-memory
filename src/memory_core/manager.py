from pathlib import Path
from loguru import logger
from .database import Database
from .short_term import ShortTermMemory
from .long_term import LongTermMemory
from .summarizer import SummaryGenerator
from .retriever import MemoryRetriever
from .llm_client import create_llm_client, LLMClient
from .models import Message, Summary
from .embedding_client import EmbeddingClient
from .vector_store import VectorStore
from .knowledge_extractor import KnowledgeExtractor


class MemoryManager:
    def __init__(
        self,
        db_path: Path | str | None = None,
        llm_client: LLMClient | None = None,
        llm_provider: str = "ollama",
        ollama_model: str = "qwen2.5:7b",
        ollama_base_url: str = "http://localhost:11434",
        ollama_timeout: float = 300.0,
        ollama_keep_alive: str = "10m",
        anthropic_api_key: str | None = None,
        anthropic_model: str = "claude-sonnet-4-20250514",
        short_term_window_size: int = 20,
        max_context_tokens: int = 8000,
        summary_trigger_threshold: int = 50,
        embedding_model: str = "embeddinggemma:300m",
        enable_vector_search: bool = True,
        enable_knowledge_extraction: bool = True,
    ):
        logger.info("Initializing MemoryManager")
        logger.debug(f"Config: db_path={db_path}, llm_provider={llm_provider}, window_size={short_term_window_size}, max_tokens={max_context_tokens}, trigger_threshold={summary_trigger_threshold}")
        self.db = Database(db_path)
        self.db_path = Path(db_path) if db_path else Path("memory.db")

        if llm_client:
            logger.debug("Using provided LLM client")
            self._llm = llm_client
        else:
            logger.debug("Creating new LLM client")
            self._llm = create_llm_client(
                provider=llm_provider,
                ollama_model=ollama_model,
                ollama_base_url=ollama_base_url,
                ollama_timeout=ollama_timeout,
                ollama_keep_alive=ollama_keep_alive,
                anthropic_api_key=anthropic_api_key,
                anthropic_model=anthropic_model,
            )
        self.short_term = ShortTermMemory(
            self.db, window_size=short_term_window_size, max_tokens=max_context_tokens
        )
        self.summarizer = SummaryGenerator(self._llm)
        self.long_term = LongTermMemory(
            self.db, self.summarizer, trigger_threshold=summary_trigger_threshold
        )
        self.retriever = MemoryRetriever(self.db)

        # 向量检索
        self.enable_vector_search = enable_vector_search
        self.embedding_client = None
        self.vector_store = None
        if enable_vector_search:
            try:
                self.embedding_client = EmbeddingClient(model=embedding_model, base_url=ollama_base_url)
                self.vector_store = VectorStore(self.db_path, dimension=self.embedding_client.dimension)
                logger.info("Vector search enabled")
            except Exception as e:
                logger.warning(f"Failed to initialize vector search: {e}")
                self.enable_vector_search = False

        # 知识提取
        self.enable_knowledge_extraction = enable_knowledge_extraction
        self.knowledge_extractor = None
        if enable_knowledge_extraction:
            self.knowledge_extractor = KnowledgeExtractor(self._llm)
            logger.info("Knowledge extraction enabled")

        logger.info("MemoryManager initialization complete")

    def start_session(self, session_id: str):
        logger.info(f"Starting session: {session_id}")
        self.db.create_session(session_id)

    def add_message(self, session_id: str, role: str, content: str) -> Message:
        logger.debug(f"MemoryManager.add_message: session={session_id}, role={role}")
        self.db.create_session(session_id)
        self.db.update_session_activity(session_id)
        message = self.short_term.add(session_id, role, content)
        logger.info(f"Message added to session {session_id}: id={message.id}")

        # 实时生成 embedding 并索引
        if self.enable_vector_search and self.embedding_client and self.vector_store:
            try:
                embedding = self.embedding_client.embed(content)
                self.vector_store.add(message.id, embedding)
                logger.debug(f"Message {message.id} indexed in vector store")
            except Exception as e:
                logger.warning(f"Failed to index message: {e}")

        if self.long_term.should_summarize(session_id):
            logger.info(f"Summary threshold reached for session {session_id}, triggering summarization")
            self.trigger_summary(session_id)
        return message

    def get_context(self, session_id: str, max_tokens: int | None = None) -> dict:
        logger.debug(f"MemoryManager.get_context: session={session_id}, max_tokens={max_tokens}")
        summaries_text = self.long_term.get_combined_summary_text(session_id)
        recent_messages = self.short_term.get_within_token_limit(session_id, max_tokens)
        context = {
            "summaries": summaries_text,
            "messages": [
                {"role": m.role, "content": m.content} for m in recent_messages
            ],
        }
        logger.info(f"Context retrieved: summaries_len={len(summaries_text)}, messages_count={len(recent_messages)}")
        return context

    def search_memory(self, query: str, session_id: str | None = None, fuzzy: bool = False, threshold: int = 60) -> list[Message]:
        logger.debug(f"MemoryManager.search_memory: query='{query}', session={session_id}, fuzzy={fuzzy}")
        results = self.retriever.search(query, session_id, fuzzy=fuzzy, threshold=threshold)
        logger.info(f"Memory search completed: {len(results)} results")
        return results

    def trigger_summary(self, session_id: str) -> Summary | None:
        logger.info(f"Triggering summary for session: {session_id}")
        messages = self.db.get_unsummarized_messages(session_id)
        if not messages:
            logger.debug("No unsummarized messages to summarize")
            return None
        logger.debug(f"Summarizing {len(messages)} messages")
        return self.long_term.create_summary(session_id, messages)

    def end_session(self, session_id: str) -> Summary | None:
        logger.info(f"Ending session: {session_id}")
        summary = self.trigger_summary(session_id)
        self.db.end_session(session_id)
        if summary:
            logger.info(f"Session {session_id} ended with summary: id={summary.id}")
        else:
            logger.info(f"Session {session_id} ended without summary")
        return summary

    def get_session(self, session_id: str):
        logger.debug(f"MemoryManager.get_session: {session_id}")
        return self.db.get_session(session_id)

    def vector_search(self, query: str, k: int = 10) -> list[tuple[Message, float]]:
        """使用向量相似度搜索消息"""
        if not self.enable_vector_search or not self.embedding_client or not self.vector_store:
            logger.warning("Vector search not available")
            return []

        try:
            query_embedding = self.embedding_client.embed(query)
            results = self.vector_store.search(query_embedding, k=k)

            messages_with_scores = []
            for msg_id, score in results:
                msg = self.db.get_message_by_id(msg_id)
                if msg:
                    messages_with_scores.append((msg, score))

            logger.info(f"Vector search for '{query[:50]}...': {len(messages_with_scores)} results")
            return messages_with_scores
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []

    def extract_knowledge(self, session_id: str) -> dict:
        """从最近的消息中提取结构化知识"""
        if not self.enable_knowledge_extraction or not self.knowledge_extractor:
            logger.warning("Knowledge extraction not available")
            return {}

        messages = self.db.get_unsummarized_messages(session_id)
        if not messages:
            messages = self.short_term.get_recent(session_id)

        if not messages:
            return self.knowledge_extractor._empty_knowledge()

        knowledge = self.knowledge_extractor.extract(messages)

        # 保存到数据库
        self.db.save_knowledge(session_id, knowledge)
        return knowledge

    def get_knowledge(self, session_id: str | None = None) -> dict:
        """获取累积的结构化知识"""
        return self.db.get_knowledge(session_id)

    def get_enriched_context(self, session_id: str, query: str | None = None, max_tokens: int | None = None) -> dict:
        """获取增强的上下文：短期记忆 + 长期总结 + 向量检索 + 结构化知识"""
        context = self.get_context(session_id, max_tokens)

        # 添加向量检索结果
        if query and self.enable_vector_search:
            vector_results = self.vector_search(query, k=5)
            context["related_memories"] = [
                {"role": msg.role, "content": msg.content[:200], "score": score}
                for msg, score in vector_results
            ]

        # 添加结构化知识
        knowledge = self.get_knowledge(session_id)
        if knowledge:
            context["knowledge"] = knowledge

        return context
