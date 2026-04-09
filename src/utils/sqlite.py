from __future__ import annotations

import sqlite3
import threading
import traceback
from pathlib import Path


class TranslationReuseCache:
    """基于 SQLite 的翻译复用缓存。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._initialized = False

    def _print_error(self, stage: str, exc: Exception) -> None:
        print(f"[TranslationReuseCache] {stage} failed")
        print(f"[TranslationReuseCache] db_path={self.db_path}")
        print(f"[TranslationReuseCache] error={type(exc).__name__}: {exc}")
        traceback.print_exc()

    def _connect(self) -> sqlite3.Connection:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            try:
                conn.row_factory = sqlite3.Row
            except Exception:
                pass
            return conn
        except Exception as exc:
            self._print_error("_connect", exc)
            raise

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS translations (
                src TEXT NOT NULL,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL DEFAULT '',
                tgt TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        rows = conn.execute("PRAGMA table_info(translations)").fetchall()
        columns = {str(row[1]) for row in rows}
        if "target_lang" not in columns:
            conn.execute("ALTER TABLE translations ADD COLUMN target_lang TEXT NOT NULL DEFAULT ''")
        if "created_at" not in columns:
            conn.execute("ALTER TABLE translations ADD COLUMN created_at TEXT")
        if "updated_at" not in columns:
            conn.execute("ALTER TABLE translations ADD COLUMN updated_at TEXT")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_translations_lookup
            ON translations (src, source_lang, target_lang)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_translations_updated
            ON translations (updated_at)
            """
        )

    def _ensure_initialized(self) -> bool:
        if self._initialized:
            return True
        with self._lock:
            if self._initialized:
                return True
            try:
                with self._connect() as conn:
                    self._ensure_schema(conn)
                    conn.commit()
                self._initialized = True
                return True
            except Exception as exc:
                self._print_error("_ensure_initialized", exc)
                return False

    @staticmethod
    def _normalize_text(text: str) -> str:
        return str(text or "").strip()

    @staticmethod
    def _normalize_lang(lang: str) -> str:
        return str(lang or "").strip().lower()

    def get(self, text: str, source_lang: str, target_lang: str) -> str | None:
        src = self._normalize_text(text)
        src_lang = self._normalize_lang(source_lang)
        tgt_lang = self._normalize_lang(target_lang)
        if not src or not src_lang or not tgt_lang:
            return None
        if not self._ensure_initialized():
            return None

        with self._lock:
            try:
                with self._connect() as conn:
                    row = conn.execute(
                        """
                        SELECT tgt
                        FROM translations
                        WHERE src = ?
                          AND source_lang = ?
                          AND (target_lang = ? OR target_lang = '')
                        ORDER BY CASE WHEN target_lang = ? THEN 0 ELSE 1 END
                        LIMIT 1
                        """,
                        (src, src_lang, tgt_lang, tgt_lang),
                    ).fetchone()
                if not row:
                    return None
                return str(row["tgt"] or "")
            except Exception as exc:
                self._print_error("get", exc)
                return None

    def save(self, text: str, source_lang: str, target_lang: str, translated: str) -> str:
        src = self._normalize_text(text)
        src_lang = self._normalize_lang(source_lang)
        tgt_lang = self._normalize_lang(target_lang)
        tgt = str(translated or "")
        if not src or not src_lang or not tgt_lang or not tgt.strip():
            return tgt
        if not self._ensure_initialized():
            return tgt

        with self._lock:
            try:
                with self._connect() as conn:
                    existing = conn.execute(
                        """
                        SELECT rowid AS entry_id
                        FROM translations
                        WHERE src = ? AND source_lang = ? AND target_lang = ?
                        LIMIT 1
                        """,
                        (src, src_lang, tgt_lang),
                    ).fetchone()
                    if existing is None:
                        existing = conn.execute(
                            """
                            SELECT rowid AS entry_id
                            FROM translations
                            WHERE src = ? AND source_lang = ? AND target_lang = ''
                            LIMIT 1
                            """,
                            (src, src_lang),
                        ).fetchone()

                    if existing is None:
                        conn.execute(
                            """
                            INSERT INTO translations (src, source_lang, target_lang, tgt, created_at, updated_at)
                            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                            """,
                            (src, src_lang, tgt_lang, tgt),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE translations
                            SET target_lang = ?, tgt = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE rowid = ?
                            """,
                            (tgt_lang, tgt, int(existing["entry_id"])),
                        )
                    conn.commit()
                return tgt
            except Exception as exc:
                self._print_error("save", exc)
                return tgt

    def list_entries(self, query: str = "", limit: int = 500, offset: int = 0) -> list[dict[str, object]]:
        if not self._ensure_initialized():
            return []
        try:
            max_rows = max(1, min(int(limit), 5000))
        except Exception:
            max_rows = 500
        try:
            start = max(0, int(offset))
        except Exception:
            start = 0

        search = self._normalize_text(query)
        params: list[object] = []
        sql = """
            SELECT
                rowid AS entry_id,
                src,
                source_lang,
                target_lang,
                tgt,
                created_at,
                updated_at
            FROM translations
        """
        if search:
            sql += """
                WHERE src LIKE ?
                   OR tgt LIKE ?
                   OR source_lang LIKE ?
                   OR target_lang LIKE ?
            """
            like = f"%{search}%"
            params.extend([like, like, like, like])
        sql += """
            ORDER BY COALESCE(updated_at, created_at, '') DESC, rowid DESC
            LIMIT ?
            OFFSET ?
        """
        params.extend([max_rows, start])

        with self._lock:
            try:
                with self._connect() as conn:
                    rows = conn.execute(sql, tuple(params)).fetchall()
                items: list[dict[str, object]] = []
                for row in rows:
                    items.append(
                        {
                            "entry_id": int(row["entry_id"]),
                            "src": str(row["src"] or ""),
                            "source_lang": str(row["source_lang"] or ""),
                            "target_lang": str(row["target_lang"] or ""),
                            "tgt": str(row["tgt"] or ""),
                            "created_at": str(row["created_at"] or ""),
                            "updated_at": str(row["updated_at"] or ""),
                        }
                    )
                return items
            except Exception as exc:
                self._print_error("list_entries", exc)
                return []

    def count_entries(self, query: str = "") -> int:
        if not self._ensure_initialized():
            return 0

        search = self._normalize_text(query)
        params: list[object] = []
        sql = "SELECT COUNT(*) AS total_count FROM translations"
        if search:
            sql += """
                WHERE src LIKE ?
                   OR tgt LIKE ?
                   OR source_lang LIKE ?
                   OR target_lang LIKE ?
            """
            like = f"%{search}%"
            params.extend([like, like, like, like])

        with self._lock:
            try:
                with self._connect() as conn:
                    row = conn.execute(sql, tuple(params)).fetchone()
                if not row:
                    return 0
                return int(row["total_count"] or 0)
            except Exception as exc:
                self._print_error("count_entries", exc)
                return 0

    def delete_entry(self, entry_id: int) -> bool:
        return self.delete_entries([entry_id]) > 0

    def clear_entries(self) -> int:
        if not self._ensure_initialized():
            return 0
        with self._lock:
            try:
                with self._connect() as conn:
                    cursor = conn.execute("DELETE FROM translations")
                    conn.commit()
                    return int(cursor.rowcount or 0)
            except Exception as exc:
                self._print_error("clear_entries", exc)
                return 0

    def delete_entries(self, entry_ids: list[int]) -> int:
        if not self._ensure_initialized():
            return 0
        ids: list[int] = []
        for value in list(entry_ids or []):
            try:
                ids.append(int(value))
            except Exception:
                continue
        if not ids:
            return 0

        placeholders = ", ".join("?" for _ in ids)
        with self._lock:
            try:
                with self._connect() as conn:
                    cursor = conn.execute(
                        f"DELETE FROM translations WHERE rowid IN ({placeholders})",
                        tuple(ids),
                    )
                    conn.commit()
                    return int(cursor.rowcount or 0)
            except Exception as exc:
                self._print_error("delete_entries", exc)
                return 0

    def get_or_save(self, text: str, source_lang: str, target_lang: str, translated: str) -> str:
        cached = self.get(text, source_lang, target_lang)
        if cached is not None:
            return cached
        return self.save(text, source_lang, target_lang, translated)
