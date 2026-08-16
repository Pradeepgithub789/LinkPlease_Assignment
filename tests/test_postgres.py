import os
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.database import Base
from app.models import DMAttemptLog, DMJob
import concurrent.futures
from datetime import datetime, timedelta

# 1. URL Normalization Test
def test_postgres_url_normalization():
    # Helper to test normalization matching database.py
    def normalize_url(url: str) -> str:
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql://", 1)
        return url

    assert normalize_url("postgres://user:pass@localhost:5432/db") == "postgresql://user:pass@localhost:5432/db"
    assert normalize_url("postgresql://user:pass@localhost:5432/db") == "postgresql://user:pass@localhost:5432/db"
    assert normalize_url("sqlite:///./linkplease.db") == "sqlite:///./linkplease.db"


# 2. Integration / Concurrency Test
TEST_PG_URL = os.environ.get("TEST_POSTGRES_URL")

@pytest.mark.skipif(not TEST_PG_URL, reason="TEST_POSTGRES_URL environment variable is not set")
def test_postgres_concurrency_rate_limiting():
    pg_url = TEST_PG_URL
    if pg_url.startswith("postgres://"):
        pg_url = pg_url.replace("postgres://", "postgresql://", 1)
    
    engine = create_engine(pg_url)
    Session = sessionmaker(bind=engine)
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    # Ensure tables are clean
    db = Session()
    try:
        db.query(DMAttemptLog).delete()
        db.query(DMJob).delete()
        db.commit()
    finally:
        db.close()
        
    num_threads = 15
    
    def worker_attempt(worker_id):
        # Emulate the rate-limiting block in dm_worker.py
        db = Session()
        try:
            # PostgreSQL advisory lock (transaction level)
            db.execute(text("SELECT pg_advisory_xact_lock(1337)"))
            
            cutoff = datetime.utcnow() - timedelta(seconds=60)
            # Count recent attempts
            recent_attempts = (
                db.query(DMAttemptLog)
                .filter(DMAttemptLog.attempted_at >= cutoff)
                .all()
            )
            
            if len(recent_attempts) >= 10:
                db.rollback()
                return False  # Rate limited
                
            # Record attempt log
            attempt_log = DMAttemptLog(job_id=worker_id)
            db.add(attempt_log)
            db.commit()
            return True  # Reserved successfully
        except Exception as e:
            db.rollback()
            return f"Error: {e}"
        finally:
            db.close()
            
    # Run concurrent threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker_attempt, i) for i in range(num_threads)]
        results = [f.result() for f in futures]
        
    # Verify results
    success_count = sum(1 for r in results if r is True)
    rate_limited_count = sum(1 for r in results if r is False)
    errors = [r for r in results if isinstance(r, str)]
    
    assert len(errors) == 0, f"Encountered unexpected errors during concurrency execution: {errors}"
    assert success_count == 10, f"Expected exactly 10 successes, got {success_count}"
    assert rate_limited_count == 5, f"Expected exactly 5 rate limits, got {rate_limited_count}"
    
    # Verify database state
    db = Session()
    try:
        attempts_in_db = db.query(DMAttemptLog).count()
        assert attempts_in_db == 10, f"Expected 10 attempts in DB, found {attempts_in_db}"
    finally:
        db.close()
        # Clean up tables
        Base.metadata.drop_all(bind=engine)
