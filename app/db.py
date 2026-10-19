import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///./hub.db")
    # Render and Heroku hand out postgres://, SQLAlchemy wants postgresql://
    # Render hands out postgres:// or postgresql://. SQLAlchemy 2.1 maps a bare
    # postgresql:// to the psycopg (v3) driver, so name the installed psycopg2 driver explicitly.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg2://" + url[len(prefix):]
    return url


DATABASE_URL = _database_url()
# Fail fast with a clear error instead of hanging forever if the database can't be reached
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {"connect_timeout": 10}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def migrate():
    """Create new tables, and add any new nullable columns to existing ones.

    Keeps an already-deployed database working when later versions add fields.
    """
    from sqlalchemy import inspect, text

    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing and col.nullable:
                    col_type = col.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}'))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
