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

    def should_summarize(self, session_id: str) -> bool:
        count = self.db.count_unsummarized_messages(session_id)
        should = count >= self.trigger_threshold
        logger.debug(f"LongTermMemory.should_summarize: session={session_id}, count={count}, threshold={self.trigger_threshold}, should={should}")
        return should

    def create_summary(self, session_id: str, messages: list[Message]) -> Summary | None:
        if not messages:
            logger.debug("No messages to create summary from")
            return None
        logger.info(f"Creating summary for {len(messages)} messages in session {session_id}")
        logger.debug(f"Message range: id={messages[0].id} to id={messages[-1].id}")
        # 获取历史摘要作为上下文
        previous_summaries = self.get_combined_summary_text(session_id)
        summary_text = self.summarizer.generate(messages, previous_context=previous_summaries)
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
