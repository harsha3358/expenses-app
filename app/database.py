import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Fetch database URL from environment, defaulting to a standard local PostgreSQL setup.
# In production/deployment, this will point to Neon PostgreSQL.
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://postgres:postgres@localhost:5432/shared_expenses"
)

# SQLite fallback is allowed for quick local testing if explicitly configured.
# Otherwise, we default to the PostgreSQL dialect.
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """
    Dependency injection helper to yield database sessions.
    Guarantees session cleanup after handling requests.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """
    Utility function to initialize all schema tables.
    """
    Base.metadata.create_all(bind=engine)
