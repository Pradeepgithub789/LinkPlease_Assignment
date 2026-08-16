from pydantic import BaseModel, Field
from typing import Optional, List

class RuleCreate(BaseModel):
    keyword: str
    dm_message: str

class RuleResponse(BaseModel):
    rule_id: str
    keyword: str
    dm_message: str

class CommentFrom(BaseModel):
    user_id: str
    username: str

class CommentData(BaseModel):
    comment_id: str
    post_id: Optional[str] = None
    text: Optional[str] = None
    created_at: Optional[str] = None
    from_user: Optional[CommentFrom] = Field(None, alias="from")

class WebhookPayload(BaseModel):
    event_id: str
    event_type: str
    sent_at: str
    data: CommentData

class StatsResponse(BaseModel):
    sent: int
    failed: int
    queued: int
    duplicates_blocked: int
