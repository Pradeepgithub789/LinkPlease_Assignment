import hmac
import hashlib
import json
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.database import get_db
from app.models import WebhookEvent

router = APIRouter()
logger = logging.getLogger(__name__)

def verify_signature(body: bytes, signature_header: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    received_signature = signature_header.split("sha256=")[1]
    expected_signature = hmac.new(
        settings.PSEUDOGRAM_API_KEY.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(received_signature, expected_signature)

@router.post("/webhook", status_code=status.HTTP_200_OK)
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()

    if settings.WEBHOOK_SIGNATURE_REQUIRED:
        signature = request.headers.get("X-PseudoGram-Signature")
        if not signature:
            logger.warning("Missing signature header")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="X-PseudoGram-Signature header missing"
            )
        if not verify_signature(body, signature):
            logger.warning("Invalid signature verification failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature"
            )

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON body"
        )

    event_id = payload.get("event_id")
    event_type = payload.get("event_type")
    sent_at_str = payload.get("sent_at")
    data = payload.get("data")

    if not event_id or not event_type or not sent_at_str or data is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required fields: event_id, event_type, sent_at, data"
        )

    # Convert ISO-8601 string to datetime
    try:
        sent_at = datetime.fromisoformat(sent_at_str.replace("Z", "+00:00"))
    except ValueError:
        sent_at = datetime.utcnow()

    # Safely extract comment details
    comment_id = data.get("comment_id")
    post_id = data.get("post_id")
    text = data.get("text")
    from_user = data.get("from", {})
    user_id = from_user.get("user_id") if isinstance(from_user, dict) else None
    username = from_user.get("username") if isinstance(from_user, dict) else None

    if not comment_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="comment_id is missing from data payload"
        )

    # Insert event with pending status
    db_event = WebhookEvent(
        event_id=event_id,
        event_type=event_type,
        comment_id=comment_id,
        post_id=post_id,
        user_id=user_id,
        username=username,
        text=text,
        sent_at=sent_at,
        status="pending"
    )

    try:
        db.add(db_event)
        db.commit()
    except IntegrityError:
        db.rollback()
        # Event already exists (event-level deduplication)
        return {"status": "ignored", "detail": "Duplicate event ID"}

    return {"status": "accepted"}
