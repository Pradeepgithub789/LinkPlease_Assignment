import pytest
from unittest.mock import patch, AsyncMock
from app.workers.dm_worker import process_queued_jobs
from app.models import DMJob, DMAttemptLog

@pytest.mark.asyncio
async def test_rate_limiting_prevents_exceeding_10_per_minute(db_session):
    # Insert 11 queued jobs
    for i in range(1, 12):
        job = DMJob(
            id=i,
            rule_id="rule-rl",
            user_id=f"user-{i}",
            comment_id=f"cmt-{i}",
            message=f"message-{i}",
            status="queued",
            attempts=0
        )
        db_session.add(job)
    db_session.commit()

    # Mock send_dm to respond with 202 Accepted
    with patch("app.workers.dm_worker.client.send_dm", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = (202, {"dm_id": "dm-rl-123"}, {})
        
        # Run process_queued_jobs. It should process 10 and then break due to rate limit capacity
        # We mock asyncio.sleep so the rate limiter sleep executes instantly if triggered,
        # but because we break from the loop, we can just assert immediately.
        with patch("app.workers.dm_worker.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await process_queued_jobs()
            
            db_session.expire_all()
            
            # Assert 10 jobs transitioned to sent_queued
            sent_jobs = db_session.query(DMJob).filter(DMJob.status == "sent_queued").all()
            assert len(sent_jobs) == 10
            
            # Assert 1 job remains queued
            remaining_queued = db_session.query(DMJob).filter(DMJob.status == "queued").all()
            assert len(remaining_queued) == 1
            assert remaining_queued[0].id == 11

            # Assert 10 attempt logs were created
            logs = db_session.query(DMAttemptLog).all()
            assert len(logs) == 10
