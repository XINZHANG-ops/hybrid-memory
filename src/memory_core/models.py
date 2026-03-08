from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Role = Literal["user", "assistant", "system"]


@dataclass
class Message:
    id: int | None
    session_id: str
    role: Role
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    token_count: int = 0
    is_summarized: bool = False


@dataclass
class Summary:
    id: int | None
    session_id: str
    summary_text: str
    message_range_start: int
    message_range_end: int
    message_count: int
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Session:
    id: int | None
    session_id: str
    started_at: datetime = field(default_factory=datetime.now)
    last_active_at: datetime = field(default_factory=datetime.now)
    is_active: bool = True
