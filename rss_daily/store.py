"""
store.py — SQLite 封装，管理已抓取文章与话题聚合。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "articles.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    """建表（幂等）。"""
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            url          TEXT UNIQUE NOT NULL,
            title        TEXT,
            source_name  TEXT,
            source_url   TEXT,
            published    TEXT,
            fetched_at   TEXT,
            content      TEXT,
            summary      TEXT,
            tags         TEXT,
            topic_id     INTEGER,
            saved_to_kb  INTEGER DEFAULT 0,
            digest_date  TEXT
        );

        CREATE TABLE IF NOT EXISTS topics (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT,
            article_ids  TEXT,
            source_count INTEGER DEFAULT 1,
            created_date TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_articles_url          ON articles(url);
        CREATE INDEX IF NOT EXISTS idx_articles_digest_date  ON articles(digest_date);
        CREATE INDEX IF NOT EXISTS idx_articles_fetched_at   ON articles(fetched_at);
        """)


class ArticleStore:
    def __init__(self):
        init_db()

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def insert(self, article: dict) -> int:
        """插入新文章，返回 rowid；url 已存在则跳过返回 -1。"""
        with _conn() as con:
            cur = con.execute(
                """
                INSERT OR IGNORE INTO articles
                  (url, title, source_name, source_url, published,
                   fetched_at, content, tags, digest_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article.get("url", ""),
                    article.get("title", ""),
                    article.get("source_name", ""),
                    article.get("source_url", ""),
                    article.get("published", ""),
                    _now_iso(),
                    article.get("content", ""),
                    json.dumps(article.get("tags", []), ensure_ascii=False),
                    article.get("digest_date", ""),
                ),
            )
            return cur.lastrowid if cur.rowcount else -1

    def update_summary(self, article_id: int, summary: str) -> None:
        with _conn() as con:
            con.execute(
                "UPDATE articles SET summary = ? WHERE id = ?",
                (summary, article_id),
            )

    def mark_saved_to_kb(self, article_id: int) -> None:
        with _conn() as con:
            con.execute(
                "UPDATE articles SET saved_to_kb = 1 WHERE id = ?",
                (article_id,),
            )

    def set_topic(self, article_id: int, topic_id: int) -> None:
        with _conn() as con:
            con.execute(
                "UPDATE articles SET topic_id = ? WHERE id = ?",
                (topic_id, article_id),
            )

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def exists(self, url: str) -> bool:
        with _conn() as con:
            row = con.execute(
                "SELECT 1 FROM articles WHERE url = ?", (url,)
            ).fetchone()
            return row is not None

    def get_by_date(self, date_str: str) -> list[dict]:
        """取指定日期（YYYY-MM-DD）收录的文章。"""
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM articles WHERE digest_date = ? ORDER BY id",
                (date_str,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_unsaved(self, date_str: str) -> list[dict]:
        """取指定日期未入知识库的文章。"""
        with _conn() as con:
            rows = con.execute(
                """SELECT * FROM articles
                   WHERE digest_date = ? AND saved_to_kb = 0
                   ORDER BY id""",
                (date_str,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_by_ids(self, ids: list[int]) -> list[dict]:
        placeholders = ",".join("?" * len(ids))
        with _conn() as con:
            rows = con.execute(
                f"SELECT * FROM articles WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_by_id(self, article_id: int) -> dict | None:
        with _conn() as con:
            row = con.execute(
                "SELECT * FROM articles WHERE id = ?", (article_id,)
            ).fetchone()
            return dict(row) if row else None
