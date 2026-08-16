import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import text
from app.config import settings
from app.database import SessionLocal, engine
from app.models import DMJob, DMAttemptLog
from app.services.client import PseudoGramClient

logger = logging.getLogger(__name__)
client = PseudoGramClient()

async def run_dm_worker():
    logger.info("Starting DM worker...")
    while True:
        try:
            await process_queued_jobs()
        except Exception as e:
            logger.error(f"Error in DM worker: {e}", exc_info=True)
        await asyncio.sleep(settings.WORKER_POLL_INTERVAL_SECONDS)

async def process_queued_jobs():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        # Fetch jobs that are queued and ready to be processed/retried
        jobs = (
            db.query(DMJob)
            .filter(
                DMJob.status == "queued",
                (DMJob.next_retry_at == None) | (DMJob.next_retry_at <= now)
            )
            .order_by(DMJob.created_at.asc())
            .all()
        )
    finally:
        db.close()

    if not jobs:
        return

    for job in jobs:
        # Step 1: Atomic Rate Limit Reservation
        db = SessionLocal()
        try:
            if db.bind.dialect.name == "sqlite":
                # We start an exclusive SQLite transaction using BEGIN IMMEDIATE to prevent race conditions.
                db.execute(text("BEGIN IMMEDIATE"))
            else:
                # PostgreSQL advisory lock (transaction-level, automatically released on commit/rollback)
                # We use a constant lock key (e.g., 1337) to serialize rate-limit checks across workers.
                db.execute(text("SELECT pg_advisory_xact_lock(1337)"))
            
            cutoff = datetime.utcnow() - timedelta(seconds=settings.RATE_LIMIT_WINDOW_SECONDS)
            # Count how many attempts we've made in the last 60 seconds
            recent_attempts = (
                db.query(DMAttemptLog)
                .filter(DMAttemptLog.attempted_at >= cutoff)
                .order_by(DMAttemptLog.attempted_at.asc())
                .all()
            )
            
            if len(recent_attempts) >= 10:
                # Rate limit (10 req / window) reached. Calculate wait time
                oldest_attempt_time = recent_attempts[0].attempted_at
                wait_seconds = settings.RATE_LIMIT_WINDOW_SECONDS - (datetime.utcnow() - oldest_attempt_time).total_seconds()
                db.rollback()
                
                if wait_seconds > 0:
                    logger.info(f"Rate limit capacity reached. Sleeping for {wait_seconds:.2f} seconds...")
                    await asyncio.sleep(wait_seconds)
                # Break to restart the queue checking loop
                break

            # Rate limit check passed. Record the attempt log
            attempt_log = DMAttemptLog(job_id=job.id)
            db.add(attempt_log)
            db.flush()

            # Step 2: Atomic status transition from 'queued' to 'sending'
            # If the status is no longer 'queued' (e.g. comment.deleted cancelled it in matching_worker),
            # this update will affect 0 rows, and we will safely skip it.
            res = db.query(DMJob).filter(DMJob.id == job.id, DMJob.status == "queued").update(
                {"status": "sending", "updated_at": datetime.utcnow()}
            )
            
            if res == 0:
                # Job was cancelled or modified. Rollback our rate limit reservation slot and skip.
                db.rollback()
                logger.info(f"Job {job.id} is no longer queued (likely cancelled). Skipping.")
                continue
            
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to reserve rate-limit slot or transition job {job.id}: {e}")
            continue
        finally:
            db.close()

        # Step 3: Call external API (outside database lock transaction)
        idempotency_key = f"dm-job-{job.id}"
        logger.info(f"Sending DM for job {job.id} (attempt {job.attempts + 1})...")
        
        status_code, data, headers = None, {}, {}
        api_error = False
        try:
            status_code, data, headers = await client.send_dm(
                recipient_user_id=job.user_id,
                message=job.message,
                comment_id=job.comment_id,
                idempotency_key=idempotency_key
            )
        except Exception as exc:
            logger.error(f"API client error for job {job.id}: {exc}")
            api_error = True

        # Step 4: Handle response & update state
        db = SessionLocal()
        try:
            # Re-fetch job in a fresh session
            db_job = db.query(DMJob).filter(DMJob.id == job.id).first()
            if not db_job:
                continue

            if api_error:
                # Network failure / Timeout -> Retry with backoff
                db_job.attempts += 1
                if db_job.attempts >= settings.MAX_DM_ATTEMPTS:
                    db_job.status = "failed"
                    db_job.last_error = "Connection timeout / network error - max attempts exceeded"
                else:
                    backoff = 2 ** (db_job.attempts - 1)
                    db_job.status = "queued"
                    db_job.next_retry_at = datetime.utcnow() + timedelta(seconds=backoff)
                    db_job.last_error = "Connection timeout / network error"
                db_job.updated_at = datetime.utcnow()
                db.commit()
                continue

            if status_code in (200, 202):
                # Accepted -> Transition to sent_queued and wait for reconciliation polling
                db_job.status = "sent_queued"
                db_job.dm_id = data.get("dm_id")
                db_job.attempts += 1
                db_job.last_error = None
                db_job.updated_at = datetime.utcnow()
                db.commit()
                logger.info(f"DM successfully accepted for job {job.id}. dm_id: {db_job.dm_id}")

            elif status_code == 429:
                # Rate limit hit (429) -> Retry based on Retry-After header
                retry_after_str = headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_str) if retry_after_str else 10.0
                except ValueError:
                    retry_after = 10.0
                
                db_job.status = "queued"
                db_job.next_retry_at = datetime.utcnow() + timedelta(seconds=retry_after)
                db_job.attempts += 1
                db_job.last_error = f"Rate limited by external API (429). Retry-After: {retry_after}s"
                db_job.updated_at = datetime.utcnow()
                db.commit()
                logger.warning(f"Rate limited by API for job {job.id}. Retrying in {retry_after}s.")

            elif status_code == 400:
                # Bad Request -> Permanent Failure
                db_job.status = "failed"
                db_job.attempts += 1
                db_job.last_error = f"Bad request (400) from API: {data.get('detail', 'No detail')}"
                db_job.updated_at = datetime.utcnow()
                db.commit()
                logger.error(f"Permanent API rejection for job {job.id}: 400 Bad Request.")

            else:
                # 500 or other unexpected errors -> Retry with backoff
                db_job.attempts += 1
                if db_job.attempts >= settings.MAX_DM_ATTEMPTS:
                    db_job.status = "failed"
                    db_job.last_error = f"API error ({status_code}) - max attempts exceeded"
                else:
                    backoff = 2 ** (db_job.attempts - 1)
                    db_job.status = "queued"
                    db_job.next_retry_at = datetime.utcnow() + timedelta(seconds=backoff)
                    db_job.last_error = f"API error ({status_code}): {data.get('detail', 'No detail')}"
                db_job.updated_at = datetime.utcnow()
                db.commit()
                logger.warning(f"API error ({status_code}) for job {job.id}. Retrying in {2 ** (db_job.attempts - 1)}s.")

        except Exception as e:
            db.rollback()
            logger.error(f"Error finalizing job status for {job.id}: {e}")
        finally:
            db.close()
