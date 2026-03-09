from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import Settings


settings = Settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_sqlite_schema_compat() -> None:
    if not settings.database_url.startswith("sqlite"):
        return

    with engine.begin() as conn:
        existing_cols = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info('message_index')")).fetchall()
        }
        if not existing_cols:
            return
        if "body_text" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN body_text TEXT"))
        if "direction" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN direction VARCHAR(16) DEFAULT 'incoming'"))
        if "in_reply_to_header" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN in_reply_to_header VARCHAR(512)"))
        if "references_header" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN references_header TEXT"))
        if "thread_key" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN thread_key VARCHAR(512)"))
        if "body_text_clean" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN body_text_clean TEXT"))
        if "safety_flags_json" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN safety_flags_json TEXT"))

        indexes = {
            row[1]
            for row in conn.execute(text("PRAGMA index_list('message_index')")).fetchall()
        }
        if "ix_message_index_thread_key" not in indexes:
            conn.execute(text("CREATE INDEX ix_message_index_thread_key ON message_index (thread_key)"))
