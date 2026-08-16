import json
import hmac
import hashlib
from app.config import settings
from app.models import WebhookEvent

def get_signature_header(body: bytes) -> str:
    sig = hmac.new(
        settings.PSEUDOGRAM_API_KEY.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    return f"sha256={sig}"

def test_webhook_successful_ingestion(client, db_session):
    payload = {
        "event_id": "evt_test_123",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {
            "comment_id": "cmt_test_123",
            "post_id": "post_test_123",
            "text": "PRICE please",
            "from": {
                "user_id": "usr_test_123",
                "username": "arjun.shoots"
            }
        }
    }
    body = json.dumps(payload).encode()
    headers = {"X-PseudoGram-Signature": get_signature_header(body)}
    
    response = client.post("/webhook", data=body, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}

    # Assert that the event was persisted in the database with pending status
    event = db_session.query(WebhookEvent).filter(WebhookEvent.event_id == "evt_test_123").first()
    assert event is not None
    assert event.event_type == "comment.created"
    assert event.comment_id == "cmt_test_123"
    assert event.status == "pending"

def test_webhook_event_level_deduplication(client, db_session):
    payload = {
        "event_id": "evt_duplicate",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {
            "comment_id": "cmt_dup_1",
            "from": {"user_id": "usr_dup", "username": "dup"}
        }
    }
    body = json.dumps(payload).encode()
    headers = {"X-PseudoGram-Signature": get_signature_header(body)}

    # Send first time
    response = client.post("/webhook", data=body, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}

    # Send second time (duplicate event_id)
    response_dup = client.post("/webhook", data=body, headers=headers)
    assert response_dup.status_code == 200
    assert response_dup.json()["status"] == "ignored"

    # Confirm only one event exists in the database
    events = db_session.query(WebhookEvent).filter(WebhookEvent.event_id == "evt_duplicate").all()
    assert len(events) == 1
