import pytest
from app.workers.matching_worker import process_pending_events
from app.models import Rule, WebhookEvent, DMJob
from datetime import datetime

def test_create_rule(client):
    response = client.post("/rules", json={"keyword": "PRICE", "dm_message": "Here is the price list!"})
    assert response.status_code == 201
    data = response.json()
    assert "rule_id" in data
    assert data["keyword"] == "PRICE"
    assert data["dm_message"] == "Here is the price list!"

def test_create_invalid_rule(client):
    response = client.post("/rules", json={"keyword": "", "dm_message": "   "})
    assert response.status_code == 400

@pytest.mark.asyncio
async def test_case_insensitive_matching(client, db_session):
    # Create rule
    rule = Rule(id="rule-1", keyword="PRICE", dm_message="Info on Price")
    db_session.add(rule)
    db_session.commit()

    # Insert comment.created event (case-insensitive: "price")
    event = WebhookEvent(
        event_id="evt-1",
        event_type="comment.created",
        comment_id="cmt-1",
        user_id="usr-123",
        username="john.doe",
        text="Can I get the price please?",
        sent_at=datetime.utcnow(),
        status="pending"
    )
    db_session.add(event)
    db_session.commit()

    # Process events manually
    await process_pending_events()

    # Assert event was processed and job was created
    db_session.expire_all()
    event_db = db_session.query(WebhookEvent).filter(WebhookEvent.event_id == "evt-1").first()
    assert event_db.status == "processed"

    job = db_session.query(DMJob).filter(DMJob.user_id == "usr-123").first()
    assert job is not None
    assert job.rule_id == "rule-1"
    assert job.message == "Info on Price"
    assert job.status == "queued"

@pytest.mark.asyncio
async def test_substring_matching(client, db_session):
    rule = Rule(id="rule-2", keyword="PRICING", dm_message="Info on Pricing")
    db_session.add(rule)
    db_session.commit()

    event = WebhookEvent(
        event_id="evt-2",
        event_type="comment.created",
        comment_id="cmt-2",
        user_id="usr-123",
        username="john.doe",
        text="Show me the PRICING-now",
        sent_at=datetime.utcnow(),
        status="pending"
    )
    db_session.add(event)
    db_session.commit()

    await process_pending_events()

    db_session.expire_all()
    job = db_session.query(DMJob).filter(DMJob.comment_id == "cmt-2").first()
    assert job is not None
    assert job.rule_id == "rule-2"

@pytest.mark.asyncio
async def test_no_matching_rule(client, db_session):
    rule = Rule(id="rule-3", keyword="PRICE", dm_message="Price message")
    db_session.add(rule)
    db_session.commit()

    event = WebhookEvent(
        event_id="evt-3",
        event_type="comment.created",
        comment_id="cmt-3",
        user_id="usr-123",
        username="john.doe",
        text="Hello world!",
        sent_at=datetime.utcnow(),
        status="pending"
    )
    db_session.add(event)
    db_session.commit()

    await process_pending_events()

    db_session.expire_all()
    event_db = db_session.query(WebhookEvent).filter(WebhookEvent.event_id == "evt-3").first()
    assert event_db.status == "ignored"

    job = db_session.query(DMJob).filter(DMJob.comment_id == "cmt-3").first()
    assert job is None
