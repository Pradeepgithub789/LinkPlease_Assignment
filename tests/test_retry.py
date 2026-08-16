import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timedelta
from app.workers.dm_worker import process_queued_jobs
from app.models import DMJob

@pytest.mark.asyncio
async def test_retry_on_500_exponential_backoff(db_session):
    job = DMJob(id=1, rule_id="r1", user_id="u1", comment_id="c1", message="hello", status="queued", attempts=0)
    db_session.add(job)
    db_session.commit()

    with patch("app.workers.dm_worker.client.send_dm", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = (500, {"detail": "Internal Server Error"}, {})
        
        await process_queued_jobs()
        
        db_session.expire_all()
        job_db = db_session.query(DMJob).filter(DMJob.id == 1).first()
        
        assert job_db.status == "queued"
        assert job_db.attempts == 1
        assert job_db.next_retry_at is not None
        diff = (job_db.next_retry_at - datetime.utcnow()).total_seconds()
        assert 0 < diff <= 2

@pytest.mark.asyncio
async def test_retry_exhausted_on_500(db_session):
    # Max attempts in testing configuration is set to 3
    job = DMJob(id=2, rule_id="r1", user_id="u2", comment_id="c2", message="hello", status="queued", attempts=2)
    db_session.add(job)
    db_session.commit()

    with patch("app.workers.dm_worker.client.send_dm", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = (500, {"detail": "Internal Server Error"}, {})
        
        await process_queued_jobs()
        
        db_session.expire_all()
        job_db = db_session.query(DMJob).filter(DMJob.id == 2).first()
        
        assert job_db.status == "failed"
        assert job_db.attempts == 3

@pytest.mark.asyncio
async def test_retry_on_429_respects_retry_after(db_session):
    job = DMJob(id=3, rule_id="r1", user_id="u3", comment_id="c3", message="hello", status="queued", attempts=0)
    db_session.add(job)
    db_session.commit()

    with patch("app.workers.dm_worker.client.send_dm", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = (429, {"detail": "Too Many Requests"}, {"Retry-After": "45"})
        
        await process_queued_jobs()
        
        db_session.expire_all()
        job_db = db_session.query(DMJob).filter(DMJob.id == 3).first()
        
        assert job_db.status == "queued"
        assert job_db.attempts == 1
        assert job_db.next_retry_at is not None
        diff = (job_db.next_retry_at - datetime.utcnow()).total_seconds()
        assert 43 <= diff <= 47

@pytest.mark.asyncio
async def test_no_retry_on_400(db_session):
    job = DMJob(id=4, rule_id="r1", user_id="u4", comment_id="c4", message="hello", status="queued", attempts=0)
    db_session.add(job)
    db_session.commit()

    with patch("app.workers.dm_worker.client.send_dm", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = (400, {"detail": "Bad Request"}, {})
        
        await process_queued_jobs()
        
        db_session.expire_all()
        job_db = db_session.query(DMJob).filter(DMJob.id == 4).first()
        
        assert job_db.status == "failed"
        assert job_db.attempts == 1
