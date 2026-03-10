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
    is_knowledge_extracted: bool = False
    is_decision_extracted: bool = False
    model: str = ""


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


@dataclass
class TokenUsage:
    id: int | None
    session_id: str
    input_tokens: int
    output_tokens: int
    model: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Interaction:
    id: int | None
    session_id: str
    type: str  # permission_request, user_choice
    tool_name: str = ""
    request_content: str = ""
    options: str = ""  # JSON array for AskUserQuestion options
    user_response: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


DecisionStatus = Literal["pending", "confirmed", "skipped"]


@dataclass
class Decision:
    id: int | None
    project: str
    session_id: str
    problem: str
    solution: str
    status: DecisionStatus = "pending"
    reason: str = ""              # 用户选择的原因
    reason_options: str = ""      # JSON array of LLM 生成的候选原因
    note: str = ""                # 用户补充说明
    files: str = ""               # JSON array of related files
    tags: str = ""                # JSON array of tags
    timestamp: datetime = field(default_factory=datetime.now)
