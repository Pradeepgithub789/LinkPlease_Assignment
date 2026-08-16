from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import DMJob, DuplicatesBlockedEvent
from app.schemas import StatsResponse

router = APIRouter()

@router.get("/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)):
    sent = db.query(func.count(DMJob.id)).filter(DMJob.status == "delivered").scalar() or 0
    failed = db.query(func.count(DMJob.id)).filter(DMJob.status == "failed").scalar() or 0
    queued = db.query(func.count(DMJob.id)).filter(DMJob.status.in_(["queued", "sending", "sent_queued"])).scalar() or 0
    duplicates_blocked = db.query(func.count(DuplicatesBlockedEvent.id)).scalar() or 0

    return StatsResponse(
        sent=sent,
        failed=failed,
        queued=queued,
        duplicates_blocked=duplicates_blocked
    )
