import pytest
from datetime import datetime
from app.workers.matching_worker import process_pending_events
from app.models import Rule, WebhookEvent, DMJob, DuplicatesBlockedEvent

@pytest.mark.asyncio
async def test_business_level_deduplication(db_session):
    rule = Rule(id="rule-p", keyword="PRICE", dm_message="Price list")
    db_session.add(rule)
    db_session.commit()

    # Event 1: User comments PRICE
    event1 = WebhookEvent(
        event_id="evt-c1", event_type="comment.created", comment_id="cmt-c1",
        user_id="user-123", username="test", text="PRICE", sent_at=datetime.utcnow(), status="pending"
    )
    # Event 2: Same user comments PRICE again later
    event2 = WebhookEvent(
        event_id="evt-c2", event_type="comment.created", comment_id="cmt-c2",
        user_id="user-123", username="test", text="PRICE please", sent_at=datetime.utcnow(), status="pending"
    )
    db_session.add_all([event1, event2])
    db_session.commit()

    await process_pending_events()

    db_session.expire_all()
    
    # Assert only 1 DMJob was created
    jobs = db_session.query(DMJob).filter(DMJob.user_id == "user-123", DMJob.rule_id == "rule-p").all()
    assert len(jobs) == 1

    # Assert duplicate blocked event was registered
    blocks = db_session.query(DuplicatesBlockedEvent).filter(DuplicatesBlockedEvent.user_id == "user-123").all()
    assert len(blocks) == 1
    assert blocks[0].event_id == "evt-c2"

@pytest.mark.asyncio
async def test_same_user_different_rules(db_session):
    rule1 = Rule(id="rule-p", keyword="PRICE", dm_message="Price list")
    rule2 = Rule(id="rule-d", keyword="DISCOUNT", dm_message="Discount list")
    db_session.add_all([rule1, rule2])
    db_session.commit()

    event = WebhookEvent(
        event_id="evt-both", event_type="comment.created", comment_id="cmt-b",
        user_id="user-123", username="test", text="What is the PRICE and DISCOUNT?", sent_at=datetime.utcnow(), status="pending"
    )
    db_session.add(event)
    db_session.commit()

    await process_pending_events()

    db_session.expire_all()
    
    # Assert user gets jobs for both rules
    jobs = db_session.query(DMJob).filter(DMJob.user_id == "user-123").all()
    assert len(jobs) == 2
    rule_ids = {j.rule_id for j in jobs}
    assert rule_ids == {"rule-p", "rule-d"}
