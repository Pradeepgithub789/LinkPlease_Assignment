import asyncio
import logging
from datetime import datetime, timedelta
from app.config import settings
from app.database import SessionLocal
from app.models import DMJob
from app.services.client import PseudoGramClient

logger = logging.getLogger(__name__)
client = PseudoGramClient()

async def run_reconciliation_worker():
    logger.info("Starting reconciliation worker...")
    while True:
        try:
            await reconcile_dm_jobs()
        except Exception as e:
            logger.error(f"Error in reconciliation worker: {e}", exc_info=True)
        await asyncio.sleep(settings.RECONCILIATION_INTERVAL_SECONDS)

async def reconcile_dm_jobs():
    db = SessionLocal()
    try:
        # Get all jobs in sent_queued status
        jobs = db.query(DMJob).filter(DMJob.status == "sent_queued").all()
        if not jobs:
            return

        for job in jobs:
            if not job.dm_id:
                # If dm_id is missing, we cannot reconcile. Mark failed.
                job.status = "failed"
                job.last_error = "Missing dm_id in sent_queued state"
                job.updated_at = datetime.utcnow()
                db.commit()
                continue

            logger.info(f"Reconciling status for job {job.id} (dm_id: {job.dm_id})...")
            
            # Increment reconciliation polls counter
            job.reconciliation_polls += 1
            if job.reconciliation_polls >= settings.MAX_RECONCILIATION_POLLS:
                job.status = "failed"
                job.last_error = "Reconciliation polling limit exceeded. Job marked failed."
                job.updated_at = datetime.utcnow()
                db.commit()
                logger.error(f"Job {job.id} exceeded max reconciliation polls ({settings.MAX_RECONCILIATION_POLLS}). Marked failed.")
                continue

            db.commit() # Save the reconciliation_polls increment

            status_code, data = None, {}
            api_error = False
            try:
                status_code, data = await client.get_dm_status(job.dm_id)
            except Exception as e:
                logger.error(f"API reconciliation call failed for job {job.id}: {e}")
                api_error = True

            if api_error or status_code != 200:
                # Let next cycle retry polling, do not fail yet
                logger.warning(f"Failed to fetch DM status for job {job.id} (code: {status_code}). Will retry later.")
                continue

            remote_status = data.get("status")
            if remote_status == "delivered":
                # Success!
                job.status = "delivered"
                job.last_error = None
                job.updated_at = datetime.utcnow()
                db.commit()
                logger.info(f"Job {job.id} (dm_id: {job.dm_id}) confirmed delivered.")

            elif remote_status == "failed":
                # Delivery failed -> Retry sending the DM again (or fail if attempts exhausted)
                job.attempts += 1
                if job.attempts >= settings.MAX_DM_ATTEMPTS:
                    job.status = "failed"
                    job.last_error = "Reconciliation reported failure - max attempts reached"
                else:
                    backoff = 2 ** (job.attempts - 1)
                    job.status = "queued"
                    job.next_retry_at = datetime.utcnow() + timedelta(seconds=backoff)
                    job.last_error = "Reconciliation reported failure. Retrying send."
                job.updated_at = datetime.utcnow()
                db.commit()
                logger.warning(f"Job {job.id} (dm_id: {job.dm_id}) delivery failed. Status: {job.status}.")
                
            elif remote_status == "queued":
                # Still in queue -> Wait for next reconciliation cycle
                logger.info(f"Job {job.id} (dm_id: {job.dm_id}) still queued in external service.")
                
            else:
                logger.warning(f"Unknown status '{remote_status}' received for job {job.id}.")

    finally:
        db.close()
