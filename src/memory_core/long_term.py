from datetime import datetime
from loguru import logger
from .database import Database
from .models import Message, Summary
from .summarizer import SummaryGenerator


class LongTermMemory:
    def __init__(self, db: Database, summarizer: SummaryGenerator, trigger_threshold: int = 50):
        self.db = db
        self.summarizer = summarizer
        self.trigger_threshold = trigger_threshold
        logger.info(f"LongTermMemory initialized: trigger_threshold={trigger_threshold}")

    def should_summarize(self, session_id: str | None = None) -> bool:
        """检查是否应该触发总结。跨所有 session 计数（session_id 参数保留但不影响计数）"""
        # 跨所有 session 计数，避免新开会话导致计数重置
        count = self.db.count_unsummarized_messages(None)
        should = count >= self.trigger_threshold
        logger.debug(f"LongTermMemory.should_summarize: total_unsummarized={count}, threshold={self.trigger_threshold}, should={should}")
        return should

    def create_summary(self, session_id: str, messages: list[Message]) -> Summary | None:
        if not messages:
            logger.debug("No messages to create summary from")
            return None
        logger.info(f"Creating summary for {len(messages)} messages in session {session_id}")
        logger.debug(f"Message range: id={messages[0].id} to id={messages[-1].id}")
        # 获取所有历史摘要作为上下文（跨 session）
        previous_summaries = self.get_all_summaries_text()
        # 获取相关时间范围内的 interactions
        start_time = messages[0].timestamp if messages else None
        end_time = messages[-1].timestamp if messages else None
        interactions = self.db.get_interactions(session_id, start_time, end_time) if start_time and end_time else []
        logger.debug(f"Found {len(interactions)} interactions for summary")
        summary_text = self.summarizer.generate(messages, previous_context=previous_summaries, interactions=interactions)
        summary = Summary(
            id=None,
            session_id=session_id,
            summary_text=summary_text,
            message_range_start=messages[0].id,
            message_range_end=messages[-1].id,
            message_count=len(messages),
            created_at=datetime.now(),
        )
        saved = self.db.add_summary(summary)
        logger.info(f"Summary saved: id={saved.id}")
        message_ids = [m.id for m in messages if m.id is not None]
        logger.debug(f"Marking {len(message_ids)} messages as summarized")
        self.db.mark_messages_summarized(message_ids)
        return saved

    def get_summaries(self, session_id: str) -> list[Summary]:
        logger.debug(f"LongTermMemory.get_summaries: session={session_id}")
        summaries = self.db.get_summaries(session_id)
        logger.debug(f"Retrieved {len(summaries)} summaries")
        return summaries

    def get_combined_summary_text(self, session_id: str) -> str:
        logger.debug(f"LongTermMemory.get_combined_summary_text: session={session_id}")
        summaries = self.get_summaries(session_id)
        if not summaries:
            logger.debug("No summaries found")
            return ""
        combined = "\n\n---\n\n".join(s.summary_text for s in summaries)
        logger.debug(f"Combined {len(summaries)} summaries: {len(combined)} chars")
        return combined

    def get_all_summaries_text(self, limit: int = 3, max_chars: int = 2000) -> str:
        """获取所有 session 的摘要（跨 session），限制长度避免 prompt 过长"""
        logger.debug(f"LongTermMemory.get_all_summaries_text: limit={limit}, max_chars={max_chars}")
        summaries = self.db.get_all_summaries(limit=limit)
        if not summaries:
            logger.debug("No summaries found across all sessions")
            return ""
        # 按时间正序排列（最旧的在前）
        summaries = sorted(summaries, key=lambda s: s.created_at)
        # 每个摘要截取一部分，避免过长
        truncated = []
        for s in summaries:
            text = s.summary_text[:600] if len(s.summary_text) > 600 else s.summary_text
            truncated.append(text)
        combined = "\n---\n".join(truncated)
        # 最终限制总长度
        if len(combined) > max_chars:
            combined = combined[:max_chars] + "..."
        logger.debug(f"Combined {len(summaries)} summaries: {len(combined)} chars")
        return combined
