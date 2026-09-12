"""SQLite 数据库连接"""
from sqlalchemy import create_engine, event, inspect as sqlalchemy_inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        inspector = sqlalchemy_inspect(connection)
        if not inspector.has_table("episodes"):
            return
        episode_columns = {c["name"] for c in inspector.get_columns("episodes")}
        migrations = {
            "name": "name VARCHAR",
            "progress_current": "progress_current INTEGER DEFAULT 0",
            "progress_total": "progress_total INTEGER DEFAULT 0",
            "error_message": "error_message VARCHAR",
            "segments_json": "segments_json TEXT",
            "intro_text": "intro_text TEXT",
            "outro_text": "outro_text TEXT",
            "processing_heartbeat": "processing_heartbeat DATETIME",
        }
        for col, ddl in migrations.items():
            if col not in episode_columns:
                connection.execute(text(f"ALTER TABLE episodes ADD COLUMN {ddl}"))
