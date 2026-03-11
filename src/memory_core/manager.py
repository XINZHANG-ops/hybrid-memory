import threading
from pathlib import Path
from loguru import logger
from .database import Database
from .short_term import ShortTermMemory
from .long_term import LongTermMemory
from .summarizer import SummaryGenerator
from .retriever import MemoryRetriever
from .llm_client import create_llm_client, LLMClient
from .models import Message, Summary, Decision
from .embedding_client import EmbeddingClient
from .vector_store import VectorStore
from .knowledge_extractor import KnowledgeExtractor
from .events import publish_event
from .content_processor import ContentConfig

# 搜索结果预览截断长度
SEARCH_PREVIEW_LENGTH = 300


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
        embedding_base_url: str = "",  # 空=使用 ollama_base_url
        enable_vector_search: bool = True,
        enable_knowledge_extraction: bool = True,
        # 知识提取配置
        knowledge_max_items_per_category: int = 10,
        # 内容处理配置
        content_include_thinking: bool = False,
        content_include_tool: bool = True,
        content_include_text: bool = True,
        content_max_chars_thinking: int = 200,
        content_max_chars_tool: int = 300,
        content_max_chars_text: int = 500,
        # Prompt 模板
        summary_prompt_template: str = "",
        knowledge_extraction_prompt: str = "",
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
        # 创建统一的内容处理配置
        self.content_config = ContentConfig(
            include_thinking=content_include_thinking,
            include_tool=content_include_tool,
            include_text=content_include_text,
            max_chars_thinking=content_max_chars_thinking,
            max_chars_tool=content_max_chars_tool,
            max_chars_text=content_max_chars_text,
        )
        self.summarizer = SummaryGenerator(
            self._llm,
            content_config=self.content_config,
        )
        self.long_term = LongTermMemory(
            self.db, self.summarizer, trigger_threshold=summary_trigger_threshold
        )
        self.retriever = MemoryRetriever(self.db)

        # 保存配置供其他模块使用
        self.summary_prompt_template = summary_prompt_template
        self.knowledge_extraction_prompt = knowledge_extraction_prompt

        # 向量检索
        self.enable_vector_search = enable_vector_search
        self.embedding_client = None
        self.vector_store = None
        self.decision_vector_store = None
        if enable_vector_search:
            try:
                # 使用 embedding_base_url，如果为空则回退到 ollama_base_url
                embed_url = embedding_base_url if embedding_base_url else ollama_base_url
                self.embedding_client = EmbeddingClient(model=embedding_model, base_url=embed_url)
                self.vector_store = VectorStore(self.db_path, dimension=self.embedding_client.dimension, store_type="message")
                self.decision_vector_store = VectorStore(self.db_path, dimension=self.embedding_client.dimension, store_type="decision")
                logger.info(f"Vector search enabled: model={embedding_model}, url={embed_url}")
            except Exception as e:
                logger.warning(f"Failed to initialize vector search: {e}")
                self.enable_vector_search = False

        # 知识提取
        self.enable_knowledge_extraction = enable_knowledge_extraction
        self.knowledge_max_items_per_category = knowledge_max_items_per_category
        self.knowledge_extractor = None
        if enable_knowledge_extraction:
            self.knowledge_extractor = KnowledgeExtractor(
                self._llm,
                content_config=self.content_config,
                extraction_prompt=knowledge_extraction_prompt,
                max_items_per_category=knowledge_max_items_per_category,
            )
            logger.info(f"Knowledge extraction enabled (max_items={knowledge_max_items_per_category})")

        logger.info("MemoryManager initialization complete")

    def start_session(self, session_id: str):
        """启动会话（不创建数据库记录，等待第一条消息时才创建）"""
        logger.info(f"Starting session: {session_id}")
        # 不在这里创建 session，等 add_message 时才创建
        # 这样避免创建空 session
        # 如果 session 已存在（resume 场景），更新活动时间
        if self.db.get_session(session_id):
            self.db.update_session_activity(session_id)

    def add_message(self, session_id: str, role: str, content: str, model: str = "", auto_summarize: bool = True) -> Message:
        """添加消息到会话

        Args:
            auto_summarize: 是否在达到阈值时自动触发总结。
                           在 hook 中应设为 False，因为 hook 进程很快退出，后台线程会被终止。
        """
        logger.debug(f"MemoryManager.add_message: session={session_id}, role={role}, model={model}, auto_summarize={auto_summarize}")
        self.db.create_session(session_id)
        self.db.update_session_activity(session_id)
        message = self.short_term.add(session_id, role, content, model=model)
        logger.info(f"Message added to session {session_id}: id={message.id}, model={model}")

        # Embedding 由后台进程 (background_summary.py) 统一处理
        # 不在 hook 中异步执行，避免与后台进程竞争

        if auto_summarize and self.long_term.should_summarize(session_id):
            logger.info(f"Summary threshold reached for session {session_id}, triggering background summarization")
            # 在后台线程中执行总结，避免阻塞调用者
            thread = threading.Thread(
                target=self._background_summary,
                args=(session_id,),
                daemon=True
            )
            thread.start()
            logger.debug("Background summary thread started")
        return message

    def index_pending_messages(self) -> int:
        """为尚未索引的消息生成 embedding（用于后台补充）"""
        if not self.enable_vector_search or not self.embedding_client or not self.vector_store:
            return 0

        # 获取已索引的消息 ID
        indexed_ids = self.vector_store.get_indexed_ids()

        # 获取所有消息，找出未索引的
        with self.db._connect() as conn:
            rows = conn.execute("SELECT id, content FROM messages ORDER BY id DESC LIMIT 100").fetchall()

        count = 0
        db_name = self.db_path.stem
        source_tag = "[Global]" if "global" in db_name.lower() else f"[{db_name}]"

        for row in rows:
            msg_id = row["id"]
            if msg_id in indexed_ids:
                continue

            try:
                content = row["content"]
                publish_event("embedding", f"{source_tag} Embedding #{msg_id}", "")
                embedding = self.embedding_client.embed(content)
                self.vector_store.add(msg_id, embedding)
                count += 1
                logger.debug(f"Message {msg_id} indexed (batch)")
            except Exception as e:
                logger.warning(f"Failed to index message {msg_id}: {e}")

        if count > 0:
            logger.info(f"Indexed {count} pending messages")
        return count

    def _background_summary(self, session_id: str):
        """在后台线程中执行总结和知识提取"""
        try:
            logger.info(f"[Background] Starting summary for session {session_id}")
            self.trigger_summary(session_id)
            logger.info(f"[Background] Summary completed for session {session_id}")
        except Exception as e:
            logger.error(f"[Background] Summary failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

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
        """触发总结。获取所有未总结消息（跨 session），总结存储到当前 session_id。同时提取知识。"""
        logger.info(f"Triggering summary for session: {session_id}")
        # 跨所有 session 获取未总结消息，避免新开会话导致消息被忽略
        messages = self.db.get_unsummarized_messages(None)
        if not messages:
            logger.debug("No unsummarized messages to summarize")
            return None
        logger.info(f"Summarizing {len(messages)} messages across all sessions")

        # 根据数据库路径确定来源标识
        db_name = self.db_path.stem
        source_tag = "[Global]" if "global" in db_name.lower() else f"[{db_name}]"

        # 在总结前提取知识（LLM 直接融合旧知识+新对话，输出完整知识库）
        if self.enable_knowledge_extraction and self.knowledge_extractor:
            try:
                # 获取未进行知识提取的消息（独立于总结计数）
                knowledge_messages = self.db.get_messages_for_knowledge(None)
                if knowledge_messages:
                    publish_event("knowledge", f"{source_tag} Extracting knowledge from {len(knowledge_messages)} msgs", "Sending to LLM...")
                    existing_knowledge = self.db.get_knowledge()
                    knowledge = self.knowledge_extractor.extract(knowledge_messages, existing_knowledge)
                    if knowledge:
                        # 获取消息 ID 列表
                        message_ids = [m.id for m in knowledge_messages if m.id]
                        # 保存知识历史版本（含消息范围）
                        self.db.save_knowledge_history(session_id, knowledge, message_ids)
                        # 直接保存（覆盖旧知识）
                        with self.db._connect() as conn:
                            conn.execute("DELETE FROM knowledge")
                        self.db.save_knowledge(session_id, knowledge)
                        # 标记消息为已知识提取
                        if message_ids:
                            self.db.mark_messages_knowledge_extracted(message_ids)
                        total_items = sum(len(v) for v in knowledge.values())
                        logger.info(f"Knowledge updated: {total_items} items")
                        publish_event("knowledge_done", f"{source_tag} Updated: {total_items} items", "")
            except Exception as e:
                logger.warning(f"Knowledge extraction during summary failed: {e}")
                publish_event("error", f"{source_tag} Knowledge extraction failed: {e}", "")

        publish_event("summary", f"{source_tag} Summarizing {len(messages)} messages", "Sending to LLM...")
        summary = self.long_term.create_summary(session_id, messages)
        if summary:
            publish_event("summary_done", f"{source_tag} Summary #{summary.id} ({len(summary.summary_text)} chars)", "")
        return summary

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

    def bm25_search(self, query: str, session_id: str | None = None, limit: int = 20) -> list[tuple[Message, float]]:
        """使用 BM25 算法搜索消息"""
        return self.retriever.bm25_search(query, session_id, limit)

    def extract_knowledge(self, session_id: str) -> dict:
        """从最近的消息中提取结构化知识（融合已有知识）"""
        if not self.enable_knowledge_extraction or not self.knowledge_extractor:
            logger.warning("Knowledge extraction not available")
            return {}

        # 获取未进行知识提取的消息
        messages = self.db.get_messages_for_knowledge(session_id)
        if not messages:
            return self.knowledge_extractor._empty_knowledge()

        # 获取已有知识，LLM 直接融合输出
        existing_knowledge = self.db.get_knowledge()
        knowledge = self.knowledge_extractor.extract(messages, existing_knowledge)

        # 获取消息 ID 列表
        message_ids = [m.id for m in messages if m.id]
        # 保存知识历史版本（含消息范围）
        self.db.save_knowledge_history(session_id, knowledge, message_ids)
        # 直接保存（覆盖旧知识）
        with self.db._connect() as conn:
            conn.execute("DELETE FROM knowledge")
        self.db.save_knowledge(session_id, knowledge)
        # 标记消息为已知识提取
        if message_ids:
            self.db.mark_messages_knowledge_extracted(message_ids)
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
                {"role": msg.role, "content": msg.content[:SEARCH_PREVIEW_LENGTH], "score": score}
                for msg, score in vector_results
            ]

        # 添加结构化知识
        knowledge = self.get_knowledge(session_id)
        if knowledge:
            context["knowledge"] = knowledge

        return context

    # ==================== Decision 搜索相关方法 ====================

    def index_pending_decisions(self) -> int:
        """为尚未索引的 Decision 生成 embedding"""
        if not self.enable_vector_search or not self.embedding_client or not self.decision_vector_store:
            return 0

        indexed_ids = self.decision_vector_store.get_indexed_ids()
        decisions = self.db.get_decisions(status="confirmed", limit=200)

        count = 0
        db_name = self.db_path.stem
        source_tag = "[Global]" if "global" in db_name.lower() else f"[{db_name}]"

        for decision in decisions:
            if decision.id in indexed_ids:
                continue

            try:
                # 将 problem + solution 组合作为 embedding 内容
                content = f"{decision.problem}\n{decision.solution}"
                if decision.reason:
                    content += f"\n{decision.reason}"
                publish_event("embedding", f"{source_tag} Decision #{decision.id}", "")
                embedding = self.embedding_client.embed(content)
                self.decision_vector_store.add(decision.id, embedding)
                count += 1
                logger.debug(f"Decision {decision.id} indexed")
            except Exception as e:
                logger.warning(f"Failed to index decision {decision.id}: {e}")

        if count > 0:
            logger.info(f"Indexed {count} pending decisions")
        return count

    def decision_vector_search(self, query: str, k: int = 10) -> list[tuple[Decision, float]]:
        """使用向量相似度搜索 Decision"""
        if not self.enable_vector_search or not self.embedding_client or not self.decision_vector_store:
            logger.warning("Decision vector search not available")
            return []

        # 自动索引尚未索引的 decisions
        self.index_pending_decisions()

        try:
            query_embedding = self.embedding_client.embed(query)
            results = self.decision_vector_store.search(query_embedding, k=k)

            decisions_with_scores = []
            for decision_id, score in results:
                decision = self.db.get_decision_by_id(decision_id)
                if decision and decision.status == "confirmed":
                    decisions_with_scores.append((decision, score))

            logger.info(f"Decision vector search for '{query[:50]}...': {len(decisions_with_scores)} results")
            return decisions_with_scores
        except Exception as e:
            logger.error(f"Decision vector search failed: {e}")
            return []

    def decision_bm25_search(self, query: str, limit: int = 20) -> list[tuple[Decision, float]]:
        """使用 BM25 算法搜索 Decision"""
        return self.retriever.decision_bm25_search(query, limit)
