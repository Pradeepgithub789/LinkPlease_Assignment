import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Setup environment variables for test execution
os.environ["PSEUDOGRAM_API_KEY"] = "test_api_key"
os.environ["DATABASE_URL"] = "sqlite:///./test_linkplease.db"
os.environ["WEBHOOK_SIGNATURE_REQUIRED"] = "true"
os.environ["MAX_DM_ATTEMPTS"] = "3"
os.environ["MAX_RECONCILIATION_POLLS"] = "5"

from app.config import settings

# Force setting updates for test lifecycle
settings.TESTING = True
settings.DATABASE_URL = "sqlite:///./test_linkplease.db"
settings.PSEUDOGRAM_API_KEY = "test_api_key"
settings.WEBHOOK_SIGNATURE_REQUIRED = True
settings.MAX_DM_ATTEMPTS = 3
settings.MAX_RECONCILIATION_POLLS = 5
settings.WORKER_POLL_INTERVAL_SECONDS = 0.1
settings.RECONCILIATION_INTERVAL_SECONDS = 0.1

from fastapi.testclient import TestClient
from app.database import Base, get_db
from app.main import app

# Create test SQLite engine
engine = create_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    # Make sure we start with a clean file
    if os.path.exists("./test_linkplease.db"):
        try:
            os.remove("./test_linkplease.db")
        except Exception:
            pass

    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    
    # Remove file on cleanup
    if os.path.exists("./test_linkplease.db"):
        try:
            os.remove("./test_linkplease.db")
        except Exception:
            pass

@pytest.fixture(autouse=True)
def clean_db():
    connection = engine.connect()
    transaction = connection.begin()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
        transaction.commit()
    except Exception:
        transaction.rollback()
        raise
    finally:
        connection.close()

# Override FastAPI database dependency
def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
