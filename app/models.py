from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Index, text
from app.database import Base

class Rule(Base):
    __tablename__ = "rules"

    id = Column(String(255), primary_key=True)
    keyword = Column(String(255), nullable=False)
    dm_message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    event_id = Column(String(255), primary_key=True)
    event_type = Column(String(50), nullable=False)
    comment_id = Column(String(255), nullable=True)
    post_id = Column(String(255), nullable=True)
    user_id = Column(String(255), nullable=True)
    username = Column(String(255), nullable=True)
    text = Column(Text, nullable=True)
    status = Column(String(50), default="pending")  # pending, processed, ignored
    sent_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class DMJob(Base):
    __tablename__ = "dm_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(String(255), nullable=False)
    user_id = Column(String(255), nullable=False)
    comment_id = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    status = Column(String(50), default="queued")  # queued, sending, sent_queued, delivered, failed, cancelled
    attempts = Column(Integer, default=0)
    reconciliation_polls = Column(Integer, default=0)
    dm_id = Column(String(255), nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Partial unique index to enforce user+rule uniqueness only for active/delivered states.
    # This allows a cancelled job to not block future comment triggers.
    __table_args__ = (
        Index(
            "uq_user_rule_active",
            "user_id",
            "rule_id",
            unique=True,
            sqlite_where=text("status IN ('queued', 'sending', 'sent_queued', 'delivered')"),
            postgresql_where=text("status IN ('queued', 'sending', 'sent_queued', 'delivered')")
        ),
    )

class DuplicatesBlockedEvent(Base):
    __tablename__ = "duplicates_blocked_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(255), unique=True, nullable=False)
    user_id = Column(String(255), nullable=False)
    rule_id = Column(String(255), nullable=False)
    comment_id = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class DeletedComment(Base):
    __tablename__ = "deleted_comments"

    comment_id = Column(String(255), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class DMAttemptLog(Base):
    __tablename__ = "dm_attempts_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, nullable=False)
    attempted_at = Column(DateTime, default=datetime.utcnow)
