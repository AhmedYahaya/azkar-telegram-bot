"""Small async SQLite data layer for the personal Telegram bot."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database is not initialized")
        return self._db

    async def connect(self) -> None:
        Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._create_schema()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _create_schema(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL,
                username TEXT,
                role TEXT UNIQUE,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_date TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_key TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed',
                completed_at TEXT NOT NULL,
                UNIQUE(item_date, item_type, item_key, user_id)
            );

            CREATE TABLE IF NOT EXISTS daily_reminders (
                item_date TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_key TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                message_kind TEXT NOT NULL DEFAULT 'text',
                created_at TEXT NOT NULL,
                PRIMARY KEY(item_date, item_type, item_key)
            );

            CREATE TABLE IF NOT EXISTS quotes (
                quote_date TEXT PRIMARY KEY,
                author_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quote_deliveries (
                quote_date TEXT NOT NULL,
                author_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY(quote_date, author_id, recipient_id)
            );

            CREATE TABLE IF NOT EXISTS hidden_messages (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                text TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS focus (
                user_id INTEGER PRIMARY KEY,
                focus_until TEXT NOT NULL,
                notice_chat_id INTEGER,
                notice_message_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS snoozes (
                user_id INTEGER NOT NULL,
                item_date TEXT NOT NULL,
                item_key TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(user_id, item_date, item_key)
            );

            CREATE TABLE IF NOT EXISTS streak_state (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                current_streak INTEGER NOT NULL DEFAULT 0,
                last_success_date TEXT,
                last_break_notified_date TEXT
            );

            -- Kept for backwards compatibility with the first MVP version.
            CREATE TABLE IF NOT EXISTS tasbeeh_goal (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                title TEXT NOT NULL,
                target INTEGER NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasbeeh_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL CHECK(scope IN ('shared', 'personal')),
                owner_user_id INTEGER,
                created_by_user_id INTEGER,
                title TEXT NOT NULL,
                target INTEGER NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quran_plan (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                start_page INTEGER NOT NULL DEFAULT 1,
                assigned_date TEXT,
                carried_over INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quran_assignments (
                item_date TEXT PRIMARY KEY,
                start_page INTEGER NOT NULL,
                carried_over INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )
        await self.conn.commit()

        # Migrations for old databases.
        cursor = await self.conn.execute("PRAGMA table_info(users)")
        columns = await cursor.fetchall()
        await cursor.close()
        column_names = {row[1] for row in columns}
        if "role" not in column_names:
            await self.conn.execute("ALTER TABLE users ADD COLUMN role TEXT")
        await self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_role ON users(role)")

        cursor = await self.conn.execute("PRAGMA table_info(daily_reminders)")
        reminder_columns = await cursor.fetchall()
        await cursor.close()
        reminder_names = {row[1] for row in reminder_columns}
        if "message_kind" not in reminder_names:
            await self.conn.execute(
                "ALTER TABLE daily_reminders ADD COLUMN message_kind TEXT NOT NULL DEFAULT 'text'"
            )

        cursor = await self.conn.execute("PRAGMA table_info(focus)")
        focus_columns = await cursor.fetchall()
        await cursor.close()
        focus_names = {row[1] for row in focus_columns}
        if "notice_chat_id" not in focus_names:
            await self.conn.execute("ALTER TABLE focus ADD COLUMN notice_chat_id INTEGER")
        if "notice_message_id" not in focus_names:
            await self.conn.execute("ALTER TABLE focus ADD COLUMN notice_message_id INTEGER")

        cursor = await self.conn.execute("PRAGMA table_info(tasbeeh_goals)")
        tasbeeh_columns = await cursor.fetchall()
        await cursor.close()
        tasbeeh_names = {row[1] for row in tasbeeh_columns}
        if "created_by_user_id" not in tasbeeh_names:
            await self.conn.execute("ALTER TABLE tasbeeh_goals ADD COLUMN created_by_user_id INTEGER")
        await self.conn.execute(
            "UPDATE tasbeeh_goals SET created_by_user_id=owner_user_id WHERE created_by_user_id IS NULL AND owner_user_id IS NOT NULL"
        )

        # Convert the old single tasbeeh goal into the new multi-goal table once.
        cursor = await self.conn.execute("SELECT COUNT(*) AS count FROM tasbeeh_goals")
        goal_count_row = await cursor.fetchone()
        await cursor.close()
        if int(goal_count_row["count"]) == 0:
            cursor = await self.conn.execute("SELECT title, target, count FROM tasbeeh_goal WHERE id=1")
            old_goal = await cursor.fetchone()
            await cursor.close()
            if old_goal:
                await self.conn.execute(
                    """
                    INSERT INTO tasbeeh_goals(scope, owner_user_id, created_by_user_id, title, target, count, active, created_at, updated_at)
                    VALUES ('shared', NULL, NULL, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        old_goal["title"],
                        old_goal["target"],
                        old_goal["count"],
                        utc_now_iso(),
                        utc_now_iso(),
                    ),
                )
            else:
                await self.conn.execute(
                    """
                    INSERT INTO tasbeeh_goals(scope, owner_user_id, created_by_user_id, title, target, count, active, created_at, updated_at)
                    VALUES ('shared', NULL, NULL, ?, 500, 0, 1, ?, ?)
                    """,
                    ("الصلاة على النبي ﷺ", utc_now_iso(), utc_now_iso()),
                )

        await self.conn.execute(
            """
            INSERT OR IGNORE INTO quran_plan(id, start_page, assigned_date, carried_over, updated_at)
            VALUES (1, 1, NULL, 0, ?)
            """,
            (utc_now_iso(),),
        )
        await self.conn.commit()

    async def upsert_user(self, user_id: int, display_name: str, username: str | None, role: str) -> None:
        await self.conn.execute("UPDATE users SET role=NULL WHERE role=? AND user_id!=?", (role, user_id))
        await self.conn.execute(
            """
            INSERT INTO users(user_id, display_name, username, role, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name=excluded.display_name,
                username=excluded.username,
                role=excluded.role,
                last_seen_at=excluded.last_seen_at
            """,
            (user_id, display_name, username, role, utc_now_iso()),
        )
        await self.conn.commit()

    async def get_user_role(self, user_id: int) -> str | None:
        cursor = await self.conn.execute("SELECT role FROM users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return row["role"] if row else None

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        cursor = await self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def get_participants(self) -> dict[str, aiosqlite.Row]:
        cursor = await self.conn.execute(
            "SELECT * FROM users WHERE role IN ('ahmed', 'janna') ORDER BY role"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {row["role"]: row for row in rows if row["role"]}

    async def get_participant_ids(self) -> tuple[int, ...]:
        participants = await self.get_participants()
        ids: list[int] = []
        for role in ("ahmed", "janna"):
            row = participants.get(role)
            if row:
                ids.append(int(row["user_id"]))
        return tuple(ids)

    async def migrate_legacy_ids(self, ahmed_user_id: int | None, janna_user_id: int | None) -> None:
        for role, user_id in (("ahmed", ahmed_user_id), ("janna", janna_user_id)):
            if user_id is None or await self.get_user_role(user_id):
                continue
            existing = await self.get_user(user_id)
            username = existing["username"] if existing else None
            await self.upsert_user(user_id, role.title(), username, role)

    async def set_completion_status(
        self,
        item_date: str,
        item_type: str,
        item_key: str,
        user_id: int,
        status: str = "completed",
    ) -> tuple[bool, str | None]:
        cursor = await self.conn.execute(
            "SELECT status FROM completions WHERE item_date=? AND item_type=? AND item_key=? AND user_id=?",
            (item_date, item_type, item_key, user_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        previous_status = row["status"] if row else None

        if previous_status is None:
            await self.conn.execute(
                """
                INSERT INTO completions(item_date, item_type, item_key, user_id, status, completed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (item_date, item_type, item_key, user_id, status, utc_now_iso()),
            )
            await self.conn.commit()
            return True, None

        if previous_status != status:
            await self.conn.execute(
                """
                UPDATE completions SET status=?, completed_at=?
                WHERE item_date=? AND item_type=? AND item_key=? AND user_id=?
                """,
                (status, utc_now_iso(), item_date, item_type, item_key, user_id),
            )
            await self.conn.commit()
            return True, previous_status

        return False, previous_status

    async def mark_completed(
        self,
        item_date: str,
        item_type: str,
        item_key: str,
        user_id: int,
        status: str = "completed",
    ) -> bool:
        changed, previous_status = await self.set_completion_status(
            item_date, item_type, item_key, user_id, status
        )
        return previous_status is None and changed

    async def set_reminder(
        self,
        item_date: str,
        item_type: str,
        item_key: str,
        chat_id: int,
        message_id: int,
        message_kind: str = "text",
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO daily_reminders(item_date, item_type, item_key, chat_id, message_id, message_kind, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_date, item_type, item_key) DO UPDATE SET
                chat_id=excluded.chat_id,
                message_id=excluded.message_id,
                message_kind=excluded.message_kind
            """,
            (item_date, item_type, item_key, chat_id, message_id, message_kind, utc_now_iso()),
        )
        await self.conn.commit()

    async def get_reminder(self, item_date: str, item_type: str, item_key: str) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            "SELECT * FROM daily_reminders WHERE item_date=? AND item_type=? AND item_key=?",
            (item_date, item_type, item_key),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def get_statuses(
        self, item_date: str, item_type: str, item_key: str, user_ids: tuple[int, ...]
    ) -> dict[int, str]:
        if not user_ids:
            return {}
        placeholders = ",".join("?" for _ in user_ids)
        cursor = await self.conn.execute(
            f"""
            SELECT user_id, status FROM completions
            WHERE item_date=? AND item_type=? AND item_key=? AND user_id IN ({placeholders})
            """,
            (item_date, item_type, item_key, *user_ids),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {int(row["user_id"]): row["status"] for row in rows}

    async def set_quote(self, quote_date: str, author_id: int, text: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO quotes(quote_date, author_id, text, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(quote_date) DO UPDATE SET
                author_id=excluded.author_id,
                text=excluded.text,
                created_at=excluded.created_at
            """,
            (quote_date, author_id, text, utc_now_iso()),
        )
        await self.conn.commit()

    async def get_quote(self, quote_date: str) -> aiosqlite.Row | None:
        cursor = await self.conn.execute("SELECT * FROM quotes WHERE quote_date=?", (quote_date,))
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def claim_quote_delivery(self, quote_date: str, author_id: int, recipient_id: int) -> bool:
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO quote_deliveries(quote_date, author_id, recipient_id, sent_at)
            VALUES (?, ?, ?, ?)
            """,
            (quote_date, author_id, recipient_id, utc_now_iso()),
        )
        inserted = cursor.rowcount == 1
        await cursor.close()
        await self.conn.commit()
        return inserted

    async def set_hidden_message(self, text: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO hidden_messages(id, text, updated_at) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at
            """,
            (text, utc_now_iso()),
        )
        await self.conn.commit()

    async def get_hidden_message(self) -> str | None:
        cursor = await self.conn.execute("SELECT text FROM hidden_messages WHERE id=1")
        row = await cursor.fetchone()
        await cursor.close()
        return row["text"] if row else None

    async def clear_hidden_message(self) -> None:
        await self.conn.execute("DELETE FROM hidden_messages WHERE id=1")
        await self.conn.commit()

    async def set_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self.conn.commit()

    async def get_setting(self, key: str) -> str | None:
        cursor = await self.conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cursor.fetchone()
        await cursor.close()
        return row["value"] if row else None

    async def set_focus(
        self,
        user_id: int,
        focus_until: str,
        notice_chat_id: int | None = None,
        notice_message_id: int | None = None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO focus(user_id, focus_until, notice_chat_id, notice_message_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                focus_until=excluded.focus_until,
                notice_chat_id=excluded.notice_chat_id,
                notice_message_id=excluded.notice_message_id
            """,
            (user_id, focus_until, notice_chat_id, notice_message_id),
        )
        await self.conn.commit()

    async def get_focus(self, user_id: int) -> aiosqlite.Row | None:
        cursor = await self.conn.execute("SELECT * FROM focus WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def get_focus_until(self, user_id: int) -> str | None:
        row = await self.get_focus(user_id)
        return row["focus_until"] if row else None

    async def get_active_focus(self) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute("SELECT * FROM focus ORDER BY focus_until")
        rows = await cursor.fetchall()
        await cursor.close()
        return rows

    async def clear_focus(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM focus WHERE user_id=?", (user_id,))
        await self.conn.commit()

    async def upsert_snooze(self, user_id: int, item_date: str, item_key: str, remind_at: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO snoozes(user_id, item_date, item_key, remind_at, active)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(user_id, item_date, item_key) DO UPDATE SET remind_at=excluded.remind_at, active=1
            """,
            (user_id, item_date, item_key, remind_at),
        )
        await self.conn.commit()

    async def deactivate_snooze(self, user_id: int, item_date: str, item_key: str) -> None:
        await self.conn.execute(
            "UPDATE snoozes SET active=0 WHERE user_id=? AND item_date=? AND item_key=?",
            (user_id, item_date, item_key),
        )
        await self.conn.commit()

    async def get_active_snoozes(self) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute("SELECT * FROM snoozes WHERE active=1 ORDER BY remind_at")
        rows = await cursor.fetchall()
        await cursor.close()
        return rows

    async def get_snooze(self, user_id: int, item_date: str, item_key: str) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            "SELECT * FROM snoozes WHERE user_id=? AND item_date=? AND item_key=? AND active=1",
            (user_id, item_date, item_key),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def get_streak(self) -> aiosqlite.Row:
        cursor = await self.conn.execute("SELECT * FROM streak_state WHERE id=1")
        row = await cursor.fetchone()
        await cursor.close()
        if row:
            return row
        await self.conn.execute("INSERT INTO streak_state(id, current_streak) VALUES (1, 0)")
        await self.conn.commit()
        cursor = await self.conn.execute("SELECT * FROM streak_state WHERE id=1")
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def update_streak(
        self, current_streak: int, last_success_date: str | None, last_break_notified_date: str | None
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO streak_state(id, current_streak, last_success_date, last_break_notified_date)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                current_streak=excluded.current_streak,
                last_success_date=excluded.last_success_date,
                last_break_notified_date=excluded.last_break_notified_date
            """,
            (current_streak, last_success_date, last_break_notified_date),
        )
        await self.conn.commit()

    # --------------------------- Tasbeeh ---------------------------
    async def create_tasbeeh_goal(
        self,
        scope: str,
        owner_user_id: int | None,
        title: str,
        target: int,
        created_by_user_id: int | None = None,
    ) -> int:
        if scope not in {"shared", "personal"}:
            raise ValueError("scope must be shared or personal")
        if target <= 0:
            raise ValueError("target must be greater than zero")
        if scope == "personal" and owner_user_id is None:
            raise ValueError("personal goals need an owner")
        now = utc_now_iso()
        creator = created_by_user_id if created_by_user_id is not None else owner_user_id
        cursor = await self.conn.execute(
            """
            INSERT INTO tasbeeh_goals(
                scope, owner_user_id, created_by_user_id, title, target, count, active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?)
            """,
            (scope, owner_user_id, creator, title, target, now, now),
        )
        goal_id = cursor.lastrowid
        await cursor.close()
        await self.conn.commit()
        return int(goal_id)

    async def list_tasbeeh_goals(self, active_only: bool = True) -> list[aiosqlite.Row]:
        where = "WHERE active=1" if active_only else ""
        cursor = await self.conn.execute(
            f"SELECT * FROM tasbeeh_goals {where} ORDER BY id"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return rows

    async def get_tasbeeh_goal(self, goal_id: int) -> aiosqlite.Row | None:
        cursor = await self.conn.execute("SELECT * FROM tasbeeh_goals WHERE id=?", (goal_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def add_tasbeeh(self, goal_id: int, amount: int) -> tuple[aiosqlite.Row, int]:
        current = await self.get_tasbeeh_goal(goal_id)
        if not current:
            raise ValueError("Goal not found")
        if not current["active"]:
            raise ValueError("Goal is inactive")
        previous_count = int(current["count"])
        await self.conn.execute(
            "UPDATE tasbeeh_goals SET count=MIN(target, count+?), updated_at=? WHERE id=? AND active=1",
            (amount, utc_now_iso(), goal_id),
        )
        await self.conn.commit()
        goal = await self.get_tasbeeh_goal(goal_id)
        if not goal:
            raise ValueError("Goal not found after update")
        return goal, previous_count

    async def reset_tasbeeh_goal(self, goal_id: int) -> bool:
        cursor = await self.conn.execute(
            "UPDATE tasbeeh_goals SET count=0, updated_at=? WHERE id=? AND active=1",
            (utc_now_iso(), goal_id),
        )
        changed = cursor.rowcount > 0
        await cursor.close()
        await self.conn.commit()
        return changed

    async def deactivate_tasbeeh_goal(self, goal_id: int) -> bool:
        cursor = await self.conn.execute(
            "UPDATE tasbeeh_goals SET active=0, updated_at=? WHERE id=? AND active=1",
            (utc_now_iso(), goal_id),
        )
        changed = cursor.rowcount > 0
        await cursor.close()
        await self.conn.commit()
        return changed

    # --------------------------- Quran ---------------------------
    async def get_quran_plan(self) -> aiosqlite.Row:
        cursor = await self.conn.execute("SELECT * FROM quran_plan WHERE id=1")
        row = await cursor.fetchone()
        await cursor.close()
        if row:
            return row
        await self.conn.execute(
            "INSERT INTO quran_plan(id, start_page, assigned_date, carried_over, updated_at) VALUES (1, 1, NULL, 0, ?)",
            (utc_now_iso(),),
        )
        await self.conn.commit()
        cursor = await self.conn.execute("SELECT * FROM quran_plan WHERE id=1")
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def get_quran_assignment(self, item_date: str) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            "SELECT * FROM quran_assignments WHERE item_date=?", (item_date,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def set_quran_assignment(self, item_date: str, start_page: int, carried_over: bool) -> None:
        await self.conn.execute(
            """
            INSERT INTO quran_assignments(item_date, start_page, carried_over, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(item_date) DO UPDATE SET start_page=excluded.start_page, carried_over=excluded.carried_over
            """,
            (item_date, start_page, int(carried_over), utc_now_iso()),
        )
        await self.conn.commit()

    async def set_quran_plan(self, start_page: int, assigned_date: str, carried_over: bool) -> None:
        await self.conn.execute(
            """
            INSERT INTO quran_plan(id, start_page, assigned_date, carried_over, updated_at)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                start_page=excluded.start_page,
                assigned_date=excluded.assigned_date,
                carried_over=excluded.carried_over,
                updated_at=excluded.updated_at
            """,
            (start_page, assigned_date, int(carried_over), utc_now_iso()),
        )
        await self.conn.commit()

    async def day_joint_quran_complete(self, item_date: str, user_ids: tuple[int, ...]) -> bool:
        if len(user_ids) != 2:
            return False
        cursor = await self.conn.execute(
            """
            SELECT COUNT(*) AS count FROM completions
            WHERE item_date=? AND item_type='quran' AND item_key='daily'
              AND status='completed' AND user_id IN (?, ?)
            """,
            (item_date, *user_ids),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["count"]) == 2

    async def prepare_quran_assignment(
        self, item_date: str, user_ids: tuple[int, ...], first_page: int, step: int
    ) -> aiosqlite.Row:
        existing = await self.get_quran_assignment(item_date)
        if existing:
            return existing

        plan = await self.get_quran_plan()
        start_page = int(plan["start_page"])
        carried_over = False
        assigned_date = plan["assigned_date"]

        if not assigned_date:
            start_page = first_page
        elif assigned_date < item_date:
            completed_previous = await self.day_joint_quran_complete(assigned_date, user_ids)
            if completed_previous:
                # Advance by one block. The page-number helper below wraps at the
                # configured total so every assignment still contains exactly N pages.
                start_page += step
                from config import QURAN_TOTAL_PAGES
                if start_page > QURAN_TOTAL_PAGES and step > 0:
                    start_page = ((start_page - 1) % QURAN_TOTAL_PAGES) + 1
                carried_over = False
            else:
                carried_over = True

        await self.set_quran_plan(start_page, item_date, carried_over)
        await self.set_quran_assignment(item_date, start_page, carried_over)
        return (await self.get_quran_assignment(item_date))  # type: ignore[return-value]

    # --------------------------- Weekly / status ---------------------------
    async def weekly_count(
        self, start_date: str, end_date: str, item_type: str, item_key: str | None, user_id: int
    ) -> int:
        query = """
            SELECT COUNT(*) AS count FROM completions
            WHERE item_date BETWEEN ? AND ? AND item_type=? AND user_id=?
              AND status IN ('completed', 'already_taken')
        """
        params: list[object] = [start_date, end_date, item_type, user_id]
        if item_key is not None:
            query += " AND item_key=?"
            params.insert(3, item_key)
        cursor = await self.conn.execute(query, params)
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["count"])

    async def day_joint_azkar_complete(self, item_date: str, user_ids: tuple[int, ...]) -> bool:
        if len(user_ids) != 2:
            return False
        cursor = await self.conn.execute(
            """
            SELECT COUNT(*) AS count FROM completions
            WHERE item_date=? AND item_type='azkar'
              AND item_key IN ('morning','evening','sleep')
              AND status='completed' AND user_id IN (?, ?)
            """,
            (item_date, *user_ids),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["count"]) == 6

    async def get_today_completions(self, item_date: str, user_ids: tuple[int, ...]) -> list[aiosqlite.Row]:
        if not user_ids:
            return []
        placeholders = ",".join("?" for _ in user_ids)
        cursor = await self.conn.execute(
            f"""
            SELECT item_type, item_key, user_id, status FROM completions
            WHERE item_date=? AND user_id IN ({placeholders})
            ORDER BY item_type, item_key, user_id
            """,
            (item_date, *user_ids),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return rows


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
