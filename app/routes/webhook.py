import base64
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


def get_signing_secret(api_key: str) -> str:
    cleaned_key = api_key.strip().strip('"').strip("'")
    
    # The live PseudoGram simulator signs webhooks using the base64-decoded email component
    # of the developer's API key format (<base64_email>.<hex_suffix>).
    # If the key contains a dot, we decode the base64 prefix as the secret.
    if "." in cleaned_key:
        b64_part = cleaned_key.split(".")[0]
        # Pad the base64 string if necessary
        padding = len(b64_part) % 4
        if padding:
            b64_part += "=" * (4 - padding)
        try:
            return base64.b64decode(b64_part).decode("utf-8")
        except Exception:
            pass
            
    return cleaned_key


def verify_signature(body: bytes, signature_header: str) -> bool:
    if not signature_header:
        return False

    sig_header = signature_header.strip()
    if sig_header.lower().startswith("sha256="):
        received_signature = sig_header[7:].strip()
    else:
        received_signature = sig_header

    secret = get_signing_secret(settings.PSEUDOGRAM_API_KEY)
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256
    ).hexdigest()

    verified = hmac.compare_digest(
        received_signature,
        expected_signature
    )
    
    if verified:
        logger.info("Webhook signature verified successfully")
    else:
        logger.warning("Webhook signature verification failed")
        
    return verified


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def receive_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    # IMPORTANT: get the exact raw request body first
    body = await request.body()

    # Safe diagnostic logging
    secret = get_signing_secret(settings.PSEUDOGRAM_API_KEY)
    key_fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    body_len = len(body)
    body_sha256 = hashlib.sha256(body).hexdigest()
    content_type = request.headers.get("Content-Type", "")
    signature = request.headers.get("X-PseudoGram-Signature")
    signature_header_present = signature is not None

    logger.info("--- Webhook Request Received ---")
    logger.info("API key fingerprint: %s", key_fingerprint)
    logger.info("Raw body length: %d", body_len)
    logger.info("Raw body SHA256: %s", body_sha256)
    logger.info("Request Content-Type: %s", content_type)
    logger.info("X-PseudoGram-Signature header present: %s", signature_header_present)

    if signature_header_present:
        sig_header = signature.strip()
        if sig_header.lower().startswith("sha256="):
            received_sig = sig_header[7:].strip()
        else:
            received_sig = sig_header
        
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256
        ).hexdigest()
        logger.info("Received signature: %s", received_sig)
        logger.info("Expected signature: %s", expected_sig)

    if settings.WEBHOOK_SIGNATURE_REQUIRED:
        if not signature:
            logger.warning("Missing signature header")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="X-PseudoGram-Signature header missing"
            )

        if not verify_signature(body, signature):
            logger.warning("Invalid webhook signature")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature"
            )

        logger.info("Webhook signature verified successfully")

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

    try:
        sent_at = datetime.fromisoformat(
            sent_at_str.replace("Z", "+00:00")
        )
    except ValueError:
        sent_at = datetime.utcnow()

    comment_id = data.get("comment_id")
    post_id = data.get("post_id")
    text = data.get("text")

    from_user = data.get("from", {})

    user_id = (
        from_user.get("user_id")
        if isinstance(from_user, dict)
        else None
    )

    username = (
        from_user.get("username")
        if isinstance(from_user, dict)
        else None
    )

    if not comment_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="comment_id is missing from data payload"
        )

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

        return {
            "status": "ignored",
            "detail": "Duplicate event ID"
        }

    return {
        "status": "accepted"
    }