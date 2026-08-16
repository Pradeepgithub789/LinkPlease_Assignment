import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timedelta
from app.main import recover_stuck_jobs
from app.workers.dm_worker import process_queued_jobs
from app.models import DMJob
from app.config import settings

@pytest.mark.asyncio
async def test_startup_crash_recovery(db_session):
    # Setup stuck job with updated_at in the past
    stuck_time = datetime.utcnow() - timedelta(seconds=settings.STUCK_JOB_TIMEOUT_SECONDS + 10)
    
    job = DMJob(
        id=999,
        rule_id="rule-rec",
        user_id="user-rec",
        comment_id="cmt-rec",
        message="hello recovered",
        status="sending",
        attempts=1,
        created_at=stuck_time,
        updated_at=stuck_time
    )
    db_session.add(job)
    db_session.commit()

    # Trigger recovery function
    recover_stuck_jobs(db_session)

    # Refresh DB session and verify status is reverted to queued
    db_session.expire_all()
    recovered_job = db_session.query(DMJob).filter(DMJob.id == 999).first()
    assert recovered_job.status == "queued"
    assert recovered_job.last_error is not None
    assert "Recovered" in recovered_job.last_error

    # Mock send_dm call to verify it gets processed next
    with patch("app.workers.dm_worker.client.send_dm", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = (202, {"dm_id": "dm_rec_123"}, {})
        
        await process_queued_jobs()
        
        db_session.expire_all()
        processed_job = db_session.query(DMJob).filter(DMJob.id == 999).first()
        
        # Verify job successfully transitioned to sent_queued
        assert processed_job.status == "sent_queued"
        assert processed_job.dm_id == "dm_rec_123"
        
        # Verify correct stable Idempotency-Key was sent
        mock_send.assert_called_once_with(
            recipient_user_id="user-rec",
            message="hello recovered",
            comment_id="cmt-rec",
            idempotency_key="dm-job-999"
        )
