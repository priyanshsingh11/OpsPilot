from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create tables for all registered models, and add columns introduced after a table existed."""
    Base.metadata.create_all(bind=engine)
    if "checkpoint" not in {c["name"] for c in inspect(engine).get_columns("agent_runs")}:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE agent_runs ADD COLUMN checkpoint JSON"))


def check_db() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
