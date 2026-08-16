import pytest
from unittest.mock import patch, AsyncMock
from app.workers.reconciliation_worker import reconcile_dm_jobs
from app.models import DMJob

@pytest.mark.asyncio
async def test_reconciliation_delivered(db_session):
    job = DMJob(id=101, rule_id="r", user_id="u", comment_id="c", message="m", status="sent_queued", dm_id="dm_101", attempts=1)
    db_session.add(job)
    db_session.commit()

    with patch("app.workers.reconciliation_worker.client.get_dm_status", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = (200, {"status": "delivered"})
        
        await reconcile_dm_jobs()
        
        db_session.expire_all()
        job_db = db_session.query(DMJob).filter(DMJob.id == 101).first()
        
        assert job_db.status == "delivered"
        assert job_db.reconciliation_polls == 1

@pytest.mark.asyncio
async def test_reconciliation_failed_triggers_retry(db_session):
    job = DMJob(id=102, rule_id="r", user_id="u", comment_id="c", message="m", status="sent_queued", dm_id="dm_102", attempts=1)
    db_session.add(job)
    db_session.commit()

    with patch("app.workers.reconciliation_worker.client.get_dm_status", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = (200, {"status": "failed"})
        
        await reconcile_dm_jobs()
        
        db_session.expire_all()
        job_db = db_session.query(DMJob).filter(DMJob.id == 102).first()
        
        assert job_db.status == "queued"
        assert job_db.attempts == 2
        assert job_db.next_retry_at is not None

@pytest.mark.asyncio
async def test_reconciliation_failed_exhausts_attempts(db_session):
    job = DMJob(id=103, rule_id="r", user_id="u", comment_id="c", message="m", status="sent_queued", dm_id="dm_103", attempts=2)
    db_session.add(job)
    db_session.commit()

    with patch("app.workers.reconciliation_worker.client.get_dm_status", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = (200, {"status": "failed"})
        
        await reconcile_dm_jobs()
        
        db_session.expire_all()
        job_db = db_session.query(DMJob).filter(DMJob.id == 103).first()
        
        assert job_db.status == "failed"
        assert job_db.attempts == 3

@pytest.mark.asyncio
async def test_reconciliation_polls_limit(db_session):
    # Max polls in conftest is set to 5. Job starting with 4 polls will hit limit of 5.
    job = DMJob(id=104, rule_id="r", user_id="u", comment_id="c", message="m", status="sent_queued", dm_id="dm_104", reconciliation_polls=4)
    db_session.add(job)
    db_session.commit()

    await reconcile_dm_jobs()
    
    db_session.expire_all()
    job_db = db_session.query(DMJob).filter(DMJob.id == 104).first()
    assert job_db.status == "failed"
    assert job_db.reconciliation_polls == 5
