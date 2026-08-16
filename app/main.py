import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database import Base, engine
from app.routes import webhook, rules, stats
from app.workers.matching_worker import run_matching_worker
from app.workers.dm_worker import run_dm_worker
from app.workers.reconciliation_worker import run_reconciliation_worker

# Set up logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# List to keep track of running tasks so we can cancel them during shutdown
background_tasks = []

def recover_stuck_jobs(db):
    from datetime import datetime, timedelta
    from app.config import settings
    from app.models import DMJob

    cutoff = datetime.utcnow() - timedelta(seconds=settings.STUCK_JOB_TIMEOUT_SECONDS)
    stuck_jobs = (
        db.query(DMJob)
        .filter(DMJob.status == "sending", DMJob.updated_at <= cutoff)
        .all()
    )
    if stuck_jobs:
        logger.info(f"Found {len(stuck_jobs)} jobs stuck in 'sending' state. Reverting to 'queued'.")
        for job in stuck_jobs:
            job.status = "queued"
            job.last_error = "Recovered from stuck 'sending' state on application startup"
            job.next_retry_at = datetime.utcnow()
            job.updated_at = datetime.utcnow()
        db.commit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    logger.info("Initializing database tables...")
    Base.metadata.create_all(bind=engine)

    logger.info("Running startup crash recovery for stuck sending jobs...")
    from app.database import SessionLocal
    with SessionLocal() as db:
        recover_stuck_jobs(db)

    from app.config import settings
    if not settings.TESTING:
        logger.info("Starting background workers...")
        task_matching = asyncio.create_task(run_matching_worker())
        task_dm = asyncio.create_task(run_dm_worker())
        task_reconciliation = asyncio.create_task(run_reconciliation_worker())

        background_tasks.extend([task_matching, task_dm, task_reconciliation])
    
    yield
    
    # Shutdown actions
    logger.info("Stopping background workers...")
    for task in background_tasks:
        task.cancel()
    
    # Wait for cancellation to complete
    await asyncio.gather(*background_tasks, return_exceptions=True)
    background_tasks.clear()
    logger.info("Shutdown complete.")

app = FastAPI(
    title="LinkPlease API",
    description="Automated Instagram-style comment-to-DM behavior with reliable deduplication and rate limiting",
    version="1.0.0",
    lifespan=lifespan
)

# Register routes
app.include_router(webhook.router)
app.include_router(rules.router)
app.include_router(stats.router)

@app.get("/")
def read_root():
    return {"message": "LinkPlease API backend service is running"}
