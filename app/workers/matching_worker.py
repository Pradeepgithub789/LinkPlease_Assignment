import asyncio
import logging
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.database import SessionLocal
from app.models import WebhookEvent, DMJob, DeletedComment, DuplicatesBlockedEvent, Rule

logger = logging.getLogger(__name__)

async def run_matching_worker():
    logger.info("Starting matching worker...")
    while True:
        try:
            await process_pending_events()
        except Exception as e:
            logger.error(f"Error in matching worker: {e}", exc_info=True)
        await asyncio.sleep(settings.WORKER_POLL_INTERVAL_SECONDS)

async def process_pending_events():
    # Fetch a batch of pending events to process
    db = SessionLocal()
    try:
        events = (
            db.query(WebhookEvent)
            .filter(WebhookEvent.status == "pending")
            .order_by(WebhookEvent.sent_at.asc())
            .limit(50)
            .all()
        )
    finally:
        db.close()

    if not events:
        return

    for event in events:
        with SessionLocal() as session:
            try:
                # Re-fetch event inside the transaction session to prevent multi-session conflicts
                session.add(event)
                
                if event.event_type == "comment.deleted":
                    # 1. Store deleted comment to prevent out-of-order creations
                    deleted_comment = DeletedComment(comment_id=event.comment_id)
                    session.add(deleted_comment)
                    try:
                        session.flush()
                    except IntegrityError:
                        session.rollback()
                        # If comment_id already in DeletedComment, that's fine. 
                        # We must start a new transaction/continue with session.
                        session.begin()

                    # 2. Cancel pending jobs
                    jobs_to_cancel = (
                        session.query(DMJob)
                        .filter(DMJob.comment_id == event.comment_id, DMJob.status == "queued")
                        .all()
                    )
                    for job in jobs_to_cancel:
                        job.status = "cancelled"
                        job.last_error = "Comment deleted before sending"
                        job.updated_at = datetime.utcnow()

                    event.status = "processed"
                    session.commit()
                    logger.info(f"Processed deletion for comment {event.comment_id}")

                elif event.event_type == "comment.created":
                    # 1. Check if comment was already deleted (out-of-order deletion)
                    is_deleted = session.query(DeletedComment).filter(DeletedComment.comment_id == event.comment_id).first()
                    if is_deleted:
                        event.status = "ignored"
                        session.commit()
                        logger.info(f"Ignored comment {event.comment_id} because comment.deleted arrived first")
                        continue

                    # 2. Match rules
                    rules = session.query(Rule).all()
                    matched = False
                    for rule in rules:
                        comment_text = event.text or ""
                        if rule.keyword.lower() in comment_text.lower():
                            matched = True
                            # Create DM Job
                            job = DMJob(
                                rule_id=rule.id,
                                user_id=event.user_id,
                                comment_id=event.comment_id,
                                message=rule.dm_message,
                                status="queued"
                            )
                            session.add(job)
                            try:
                                session.flush()
                                logger.info(f"Queued DM job for user {event.user_id}, rule {rule.id}")
                            except IntegrityError:
                                session.rollback()
                                session.begin()
                                # User + Rule uniqueness constraint hit.
                                # Record as duplicate blocked to increment the counter
                                dup = DuplicatesBlockedEvent(
                                    event_id=event.event_id,
                                    user_id=event.user_id,
                                    rule_id=rule.id,
                                    comment_id=event.comment_id
                                )
                                session.add(dup)
                                try:
                                    session.flush()
                                    logger.info(f"Duplicate DM blocked: user {event.user_id}, rule {rule.id}")
                                except IntegrityError:
                                    session.rollback()
                                    session.begin()

                    event.status = "processed" if matched else "ignored"
                    session.commit()

                else:
                    # Ignore unknown events
                    event.status = "ignored"
                    session.commit()

            except Exception as e:
                session.rollback()
                logger.error(f"Failed to process webhook event {event.event_id}: {e}")
                # Mark as error to avoid infinite loop retries on bad records
                with SessionLocal() as err_session:
                    err_event = err_session.query(WebhookEvent).filter(WebhookEvent.event_id == event.event_id).first()
                    if err_event:
                        err_event.status = "error"
                        err_session.commit()
