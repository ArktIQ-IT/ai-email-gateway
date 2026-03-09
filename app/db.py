import json

from sqlalchemy import create_engine, or_, select, text
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


def ensure_sqlite_schema_compat() -> bool:
    if not settings.database_url.startswith("sqlite"):
        return False

    schema_changed = False
    with engine.begin() as conn:
        existing_cols = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info('message_index')")).fetchall()
        }
        if not existing_cols:
            return False
        if "body_text" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN body_text TEXT"))
            schema_changed = True
        if "direction" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN direction VARCHAR(16) DEFAULT 'incoming'"))
            schema_changed = True
        if "in_reply_to_header" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN in_reply_to_header VARCHAR(512)"))
            schema_changed = True
        if "references_header" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN references_header TEXT"))
            schema_changed = True
        if "thread_key" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN thread_key VARCHAR(512)"))
            schema_changed = True
        if "body_text_clean" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN body_text_clean TEXT"))
            schema_changed = True
        if "safety_flags_json" not in existing_cols:
            conn.execute(text("ALTER TABLE message_index ADD COLUMN safety_flags_json TEXT"))
            schema_changed = True

        indexes = {
            row[1]
            for row in conn.execute(text("PRAGMA index_list('message_index')")).fetchall()
        }
        if "ix_message_index_thread_key" not in indexes:
            conn.execute(text("CREATE INDEX ix_message_index_thread_key ON message_index (thread_key)"))
            schema_changed = True
    return schema_changed


def sqlite_message_index_needs_backfill() -> bool:
    if not settings.database_url.startswith("sqlite"):
        return False
    with SessionLocal() as db:
        from app.models import MessageIndex

        missing = db.scalar(
            select(MessageIndex.id)
            .where(
                or_(
                    MessageIndex.thread_key.is_(None),
                    MessageIndex.body_text_clean.is_(None),
                    MessageIndex.safety_flags_json.is_(None),
                )
            )
            .limit(1)
        )
    return missing is not None


def backfill_message_index_metadata(batch_size: int = 500) -> dict[str, int]:
    from app.email_safety import analyze_prompt_injection, build_thread_key, clean_email_text
    from app.models import MessageIndex

    scanned = 0
    updated = 0

    with SessionLocal() as db:
        while True:
            rows = db.scalars(
                select(MessageIndex)
                .where(
                    or_(
                        MessageIndex.thread_key.is_(None),
                        MessageIndex.body_text_clean.is_(None),
                        MessageIndex.safety_flags_json.is_(None),
                    )
                )
                .limit(batch_size)
            ).all()
            if not rows:
                break

            changed = False
            for row in rows:
                scanned += 1
                row_changed = False
                if not row.body_text_clean:
                    row.body_text_clean = clean_email_text(row.body_text)
                    row_changed = True
                if not row.thread_key:
                    row.thread_key = build_thread_key(
                        row.message_id_header,
                        row.in_reply_to_header,
                        row.references_header,
                        row.subject,
                    )
                    row_changed = True
                if not row.safety_flags_json:
                    safety = analyze_prompt_injection(row.subject, row.body_text_clean or row.body_text)
                    row.safety_flags_json = json.dumps(safety, separators=(",", ":"))
                    row_changed = True
                if row_changed:
                    updated += 1
                    changed = True

            if changed:
                db.commit()
    return {"scanned": scanned, "updated": updated}
