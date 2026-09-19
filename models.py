try:
    from pydantic import BaseModel, Field
except ImportError:
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        def model_dump(self, **kwargs):
            return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        def dict(self, **kwargs):
            return self.model_dump(**kwargs)
        @classmethod
        def parse_obj(cls, obj):
            return cls(**obj)
    def Field(default=None, **kwargs):
        return default

from typing import Optional, Any, List, Dict


# ── Hearthline Coworking Request Models ─────────────────────────────────────

class MemberRequestCreate(BaseModel):
    requester_name: str
    requester_tier: str = "standard"  # "standard" or "founding member"
    body_text: str
    submitted_at: Optional[str] = None
    request_id: Optional[str] = None


class MemberRequestResponse(BaseModel):
    request_id: str
    requester_name: str
    requester_tier: str
    status: str
    submitted_at: str
    body_text: str
    category: Optional[str] = None
    confidence: Optional[float] = None
    priority: Optional[str] = None
    priority_score: Optional[int] = 2
    assigned_team: Optional[str] = None
    llm_reasoning: Optional[str] = None
    actions_taken: Optional[List[str]] = None
    is_duplicate: Optional[bool] = False
    duplicate_of_id: Optional[str] = None
    discrepancy_checked: Optional[bool] = False
    discrepancy_verified: Optional[bool] = False
    discrepancy_details: Optional[str] = None
    courtesy_resolution: Optional[str] = None
    acknowledgment_message: Optional[str] = None
    human_override: Optional[bool] = False
    override_details: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class HumanOverrideRequest(BaseModel):
    category: Optional[str] = None
    priority: Optional[str] = None
    assigned_team: Optional[str] = None
    status: Optional[str] = None
    override_note: Optional[str] = None
    reviewer_name: Optional[str] = "Member Success Lead"


class InvoiceItem(BaseModel):
    id: Optional[int] = None
    requester_name: str
    verified_discrepancy: str


# ── Legacy Models (for backward compatibility) ──────────────────────────────

class AskRequest(BaseModel):
    question: str
    session_id: Optional[str] = None


class AskResponse(BaseModel):
    answer: str
    action_taken: Optional[str] = None
    action_result: Optional[Any] = None
    sources: Optional[List[str]] = None
    session_id: str


class ErrorResponse(BaseModel):
    error: str
    code: str


class TicketStatusUpdate(BaseModel):
    status: str


class EmployeeUpdate(BaseModel):
    leave_balance: Optional[int] = None
    role: Optional[str] = None
    department: Optional[str] = None


class ReportCreate(BaseModel):
    type: str
    period: str
    summary: str


class TicketCommentCreate(BaseModel):
    author: str
    comment: str


class MeetingTranscript(BaseModel):
    transcript: str


class MarketingContentRequest(BaseModel):
    topic: str
    content_format: str = "social post"


class RagDocumentCreate(BaseModel):
    text: str
    source: str
