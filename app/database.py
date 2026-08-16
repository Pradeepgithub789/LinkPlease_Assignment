from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

# Normalize PostgreSQL connection strings starting with postgres:// to postgresql:// for compatibility
db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

connect_args = {}
is_sqlite = db_url.startswith("sqlite")

if is_sqlite:
    connect_args["check_same_thread"] = False

engine = create_engine(db_url, connect_args=connect_args)

# Configure WAL mode and normal synchronous mode for SQLite to handle concurrency safely
if is_sqlite:
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
        conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
