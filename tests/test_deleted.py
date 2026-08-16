import pytest
from datetime import datetime
from app.workers.matching_worker import process_pending_events
from app.models import Rule, WebhookEvent, DMJob, DeletedComment

@pytest.mark.asyncio
async def test_cancel_pending_job_on_comment_deleted(db_session):
    # Setup: Active Rule and a Queued DMJob
    rule = Rule(id="rule-p", keyword="PRICE", dm_message="Info")
    db_session.add(rule)
    
    job = DMJob(id=1, rule_id="rule-p", user_id="u1", comment_id="cmt-1", message="Info", status="queued")
    db_session.add(job)
    
    # Event: comment.deleted arrives
    event = WebhookEvent(
        event_id="evt-d1",
        event_type="comment.deleted",
        comment_id="cmt-1",
        sent_at=datetime.utcnow(),
        status="pending"
    )
    db_session.add(event)
    db_session.commit()

    # Process events
    await process_pending_events()

    # Verify job status changed to cancelled
    db_session.expire_all()
    job_db = db_session.query(DMJob).filter(DMJob.id == 1).first()
    assert job_db.status == "cancelled"

@pytest.mark.asyncio
async def test_out_of_order_deletion_prevent_processing(db_session):
    # Setup: Rule
    rule = Rule(id="rule-p", keyword="PRICE", dm_message="Info")
    db_session.add(rule)
    db_session.commit()

    # Event 1: comment.deleted arrives first
    del_event = WebhookEvent(
        event_id="evt-d2", event_type="comment.deleted", comment_id="cmt-2",
        sent_at=datetime.utcnow(), status="pending"
    )
    db_session.add(del_event)
    db_session.commit()

    # Process deletion
    await process_pending_events()

    # Event 2: comment.created arrives out-of-order later
    cre_event = WebhookEvent(
        event_id="evt-c2", event_type="comment.created", comment_id="cmt-2",
        user_id="u1", username="john", text="PRICE", sent_at=datetime.utcnow(), status="pending"
    )
    db_session.add(cre_event)
    db_session.commit()

    # Process creation
    await process_pending_events()

    db_session.expire_all()
    # Confirm comment.created event was ignored
    evt_db = db_session.query(WebhookEvent).filter(WebhookEvent.event_id == "evt-c2").first()
    assert evt_db.status == "ignored"

    # Confirm no job was created for cmt-2
    job_db = db_session.query(DMJob).filter(DMJob.comment_id == "cmt-2").first()
    assert job_db is None

@pytest.mark.asyncio
async def test_deletion_does_not_cancel_delivered_job(db_session):
    rule = Rule(id="rule-p", keyword="PRICE", dm_message="Info")
    db_session.add(rule)
    
    # Job already delivered
    job = DMJob(id=2, rule_id="rule-p", user_id="u1", comment_id="cmt-3", message="Info", status="delivered")
    db_session.add(job)
    
    event = WebhookEvent(
        event_id="evt-d3", event_type="comment.deleted", comment_id="cmt-3",
        sent_at=datetime.utcnow(), status="pending"
    )
    db_session.add(event)
    db_session.commit()

    await process_pending_events()

    db_session.expire_all()
    # Status remains delivered
    job_db = db_session.query(DMJob).filter(DMJob.id == 2).first()
    assert job_db.status == "delivered"

@pytest.mark.asyncio
async def test_cancelled_job_releases_uniqueness_constraint(db_session):
    rule = Rule(id="rule-p", keyword="PRICE", dm_message="Info")
    db_session.add(rule)
    db_session.commit()

    # 1. First comment matches rule, job is queued
    evt1 = WebhookEvent(
        event_id="evt-c4a", event_type="comment.created", comment_id="cmt-4a",
        user_id="user-999", username="test", text="PRICE", sent_at=datetime.utcnow(), status="pending"
    )
    db_session.add(evt1)
    db_session.commit()
    await process_pending_events()

    db_session.expire_all()
    job1 = db_session.query(DMJob).filter(DMJob.comment_id == "cmt-4a").first()
    assert job1 is not None
    assert job1.status == "queued"

    # 2. Deletion event arrives, job is cancelled
    evt_del = WebhookEvent(
        event_id="evt-d4", event_type="comment.deleted", comment_id="cmt-4a",
        sent_at=datetime.utcnow(), status="pending"
    )
    db_session.add(evt_del)
    db_session.commit()
    await process_pending_events()

    db_session.expire_all()
    job1_after = db_session.query(DMJob).filter(DMJob.comment_id == "cmt-4a").first()
    assert job1_after.status == "cancelled"

    # 3. Same user comments "PRICE" again on a different comment cmt-4b.
    # The uniqueness constraint should allow this because the first job is cancelled!
    evt2 = WebhookEvent(
        event_id="evt-c4b", event_type="comment.created", comment_id="cmt-4b",
        user_id="user-999", username="test", text="PRICE again", sent_at=datetime.utcnow(), status="pending"
    )
    db_session.add(evt2)
    db_session.commit()
    await process_pending_events()

    db_session.expire_all()
    # Confirm a second job was successfully created
    job2 = db_session.query(DMJob).filter(DMJob.comment_id == "cmt-4b").first()
    assert job2 is not None
    assert job2.status == "queued"
    assert job2.user_id == "user-999"
