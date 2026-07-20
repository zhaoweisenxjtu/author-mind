"""Mental Model DAO: CRUD for mental_models table (L2)."""

from datetime import datetime
from database import get_connection, row_to_dict, rows_to_dicts


def insert_model(author_name: str, topic: str, title: str,
                 content_md: str, md_path: str, evidence_count: int = 0,
                 article_count: int = 0, first_seen_at: str = None) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO mental_models
               (author_name, topic, title, content_md, md_path,
                evidence_count, article_count, first_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (author_name, topic, title, content_md, md_path,
             evidence_count, article_count, first_seen_at),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_model(model_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM mental_models WHERE id = ?", (model_id,)
        ).fetchone()
        return row_to_dict(row)
    finally:
        conn.close()


def list_models(author_name: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM mental_models WHERE author_name = ? ORDER BY evidence_count DESC",
            (author_name,),
        ).fetchall()
        return rows_to_dicts(rows)
    finally:
        conn.close()


def update_model(model_id: int, triple_check: dict = None, content_md: str = None):
    conn = get_connection()
    try:
        sets = []
        params = []
        if triple_check is not None:
            import json
            sets.append("triple_check = ?")
            params.append(json.dumps(triple_check, ensure_ascii=False))
        if content_md is not None:
            sets.append("content_md = ?")
            params.append(content_md)
        if sets:
            sets.append("last_updated_at = ?")
            params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            params.append(model_id)
            conn.execute(
                f"UPDATE mental_models SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            conn.commit()
    finally:
        conn.close()


def model_exists(author_name: str, topic: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM mental_models WHERE author_name = ? AND topic = ?",
            (author_name, topic),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def count_models(author_name: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM mental_models WHERE author_name = ?",
            (author_name,),
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()
