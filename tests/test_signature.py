import json
import hmac
import hashlib
from app.config import settings

def get_signature(body: bytes, key: str) -> str:
    sig = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"

def test_valid_signature(client):
    payload = {
        "event_id": "evt_sig_ok",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {"comment_id": "cmt_sig_ok", "from": {"user_id": "usr1", "username": "u1"}}
    }
    body = json.dumps(payload).encode()
    headers = {"X-PseudoGram-Signature": get_signature(body, settings.PSEUDOGRAM_API_KEY)}
    
    response = client.post("/webhook", data=body, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}

def test_invalid_signature(client):
    payload = {
        "event_id": "evt_sig_bad",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {"comment_id": "cmt_sig_bad", "from": {"user_id": "usr1", "username": "u1"}}
    }
    body = json.dumps(payload).encode()
    # Sign with a different key
    headers = {"X-PseudoGram-Signature": get_signature(body, "wrong_api_key")}
    
    response = client.post("/webhook", data=body, headers=headers)
    assert response.status_code == 401
    assert "Invalid" in response.json()["detail"]

def test_missing_signature(client):
    payload = {
        "event_id": "evt_sig_missing",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {"comment_id": "cmt_sig_missing", "from": {"user_id": "usr1", "username": "u1"}}
    }
    body = json.dumps(payload).encode()
    
    response = client.post("/webhook", data=body)
    assert response.status_code == 401
    assert "missing" in response.json()["detail"].lower()
