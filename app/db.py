import aiosqlite
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  username TEXT,
  full_name TEXT,
  group_id INTEGER,
  notify_open INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
  group_id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  schedule_file_id TEXT,
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS group_settings (
  group_id INTEGER PRIMARY KEY,
  open_days_before INTEGER NOT NULL DEFAULT 2,
  open_time TEXT NOT NULL DEFAULT '10:00',
  close_mode TEXT NOT NULL DEFAULT 'at_start', -- at_start | minutes_before
  close_minutes_before INTEGER,
  cancel_minutes_before INTEGER NOT NULL DEFAULT 360
);

CREATE TABLE IF NOT EXISTS chats (
  chat_id INTEGER PRIMARY KEY,
  title TEXT,
  chat_type TEXT,
  is_admin INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_chats (
  group_id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slot_exceptions (
  slot_id INTEGER NOT NULL,
  starts_on TEXT NOT NULL, -- YYYY-MM-DD date to skip
  created_at TEXT NOT NULL,
  PRIMARY KEY(slot_id, starts_on)
);

CREATE TABLE IF NOT EXISTS invites (
  token TEXT PRIMARY KEY,
  group_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS training_slots (
  slot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL,
  starts_at TEXT NOT NULL,
  capacity INTEGER NOT NULL,
  base_capacity INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tournaments (
  tournament_id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  starts_at TEXT NOT NULL,
  capacity INTEGER NOT NULL,
  amount INTEGER,
  description TEXT,
  close_mode TEXT NOT NULL DEFAULT 'at_start', -- at_start | minutes_before
  close_minutes_before INTEGER,
  cancel_minutes_before INTEGER NOT NULL DEFAULT 360,
  waitlist_limit INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tournament_groups (
  tournament_id INTEGER NOT NULL,
  group_id INTEGER NOT NULL,
  PRIMARY KEY (tournament_id, group_id)
);

CREATE TABLE IF NOT EXISTS bookings (
  booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  entity_type TEXT NOT NULL, -- training | tournament
  entity_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', -- active | waitlist | cancelled
  seats INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_booking_active
ON bookings(user_id, entity_type, entity_id)
WHERE status='active';

CREATE TABLE IF NOT EXISTS payments (
  payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
  booking_id INTEGER NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending', -- pending | confirmed | rejected
  confirmed_by INTEGER,
  confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS payment_settings (
  id INTEGER PRIMARY KEY CHECK(id=1),
  text TEXT NOT NULL DEFAULT 'Оплата: уточните у тренера.',
  amount INTEGER,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_modes (
  user_id INTEGER PRIMARY KEY,
  mode TEXT
);

CREATE TABLE IF NOT EXISTS notify_settings (
  id INTEGER PRIMARY KEY CHECK(id=1),
  text TEXT NOT NULL DEFAULT 'Открыта запись на тренировку.',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notify_open_log (
  user_id INTEGER NOT NULL,
  slot_id INTEGER NOT NULL,
  sent_at TEXT NOT NULL,
  PRIMARY KEY (user_id, slot_id)
);

CREATE TABLE IF NOT EXISTS slot_full_notifications (
  slot_id INTEGER NOT NULL,
  admin_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (slot_id, admin_id)
);

CREATE TABLE IF NOT EXISTS admins (
  user_id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_invites (
  token TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1
);
"""

class DB:
    def __init__(self, path: str):
        self.path = path

    @asynccontextmanager
    async def connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        try:
            yield db
        finally:
            await db.close()

    async def init(self) -> None:
        async with self.connect() as db:
            await db.executescript(SCHEMA_SQL)
            await self._migrate(db)
            # seed payment_settings row
            cur = await db.execute("SELECT id FROM payment_settings WHERE id=1")
            row = await cur.fetchone()
            if row is None:
                await db.execute(
                    "INSERT INTO payment_settings(id, text, amount, updated_at) VALUES (1, ?, ?, ?)",
                    ("Оплата: уточните у тренера.", None, datetime.utcnow().isoformat())
                )
            # seed notify_settings row
            cur = await db.execute("SELECT id FROM notify_settings WHERE id=1")
            row = await cur.fetchone()
            if row is None:
                await db.execute(
                    "INSERT INTO notify_settings(id, text, updated_at) VALUES (1, ?, ?)",
                    ("Открыта запись на тренировку.", datetime.utcnow().isoformat())
                )
            await db.commit()

    async def _migrate(self, db: aiosqlite.Connection) -> None:
        # add new columns to tournaments if missing
        cur = await db.execute("PRAGMA table_info(tournaments)")
        rows = await cur.fetchall()
        existing = {r["name"] for r in rows}
        migrations = []
        if "amount" not in existing:
            migrations.append("ALTER TABLE tournaments ADD COLUMN amount INTEGER")
        if "close_mode" not in existing:
            migrations.append("ALTER TABLE tournaments ADD COLUMN close_mode TEXT NOT NULL DEFAULT 'at_start'")
        if "close_minutes_before" not in existing:
            migrations.append("ALTER TABLE tournaments ADD COLUMN close_minutes_before INTEGER")
        if "cancel_minutes_before" not in existing:
            migrations.append("ALTER TABLE tournaments ADD COLUMN cancel_minutes_before INTEGER NOT NULL DEFAULT 360")
        if "waitlist_limit" not in existing:
            migrations.append("ALTER TABLE tournaments ADD COLUMN waitlist_limit INTEGER NOT NULL DEFAULT 0")
        # add seats to bookings if missing
        cur = await db.execute("PRAGMA table_info(bookings)")
        rows = await cur.fetchall()
        existing_bookings = {r["name"] for r in rows}
        if "seats" not in existing_bookings:
            migrations.append("ALTER TABLE bookings ADD COLUMN seats INTEGER NOT NULL DEFAULT 1")
        # add base_capacity to training_slots if missing
        cur = await db.execute("PRAGMA table_info(training_slots)")
        rows = await cur.fetchall()
        existing_slots = {r["name"] for r in rows}
        if "base_capacity" not in existing_slots:
            migrations.append("ALTER TABLE training_slots ADD COLUMN base_capacity INTEGER NOT NULL DEFAULT 0")
        for stmt in migrations:
            await db.execute(stmt)

        # backfill base_capacity = capacity where zero
        await db.execute("UPDATE training_slots SET base_capacity=capacity WHERE base_capacity=0")

        # add notify_open to users if missing
        cur = await db.execute("PRAGMA table_info(users)")
        rows = await cur.fetchall()
        existing_users = {r["name"] for r in rows}
        if "notify_open" not in existing_users:
            await db.execute("ALTER TABLE users ADD COLUMN notify_open INTEGER NOT NULL DEFAULT 0")

    # ---------- user ----------
    async def upsert_user(self, user_id: int, username: str, full_name: str) -> None:
        async with self.connect() as db:
            await db.execute(
                """INSERT INTO users(user_id, username, full_name, created_at)
                VALUES(?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name""",
                (user_id, username, full_name, datetime.utcnow().isoformat())
            )
            await db.commit()

    async def create_guest_user(self, full_name: str, group_id: Optional[int]) -> int:
        async with self.connect() as db:
            cur = await db.execute("SELECT MIN(user_id) AS m FROM users")
            row = await cur.fetchone()
            min_id = row["m"] if row else None
            new_id = -1 if min_id is None or int(min_id) >= 0 else int(min_id) - 1
            await db.execute(
                "INSERT INTO users(user_id, username, full_name, group_id, notify_open, created_at) VALUES(?,?,?,?,?,?)",
                (new_id, "", full_name, group_id, 0, datetime.utcnow().isoformat()),
            )
            await db.commit()
            return int(new_id)

    async def set_user_group(self, user_id: int, group_id: int) -> None:
        async with self.connect() as db:
            await db.execute("UPDATE users SET group_id=? WHERE user_id=?", (group_id, user_id))
            await db.commit()

    async def get_user(self, user_id: int) -> Optional[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_user_notify_open(self, user_id: int, enabled: bool) -> None:
        async with self.connect() as db:
            await db.execute("UPDATE users SET notify_open=? WHERE user_id=?", (1 if enabled else 0, user_id))
            await db.commit()

    # ---------- mode ----------
    async def set_mode(self, user_id: int, mode: Optional[str]) -> None:
        async with self.connect() as db:
            if mode is None:
                await db.execute("DELETE FROM user_modes WHERE user_id=?", (user_id,))
            else:
                await db.execute(
                    "INSERT INTO user_modes(user_id, mode) VALUES(?, ?) ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode",
                    (user_id, mode)
                )
            await db.commit()

    async def get_mode(self, user_id: int) -> Optional[str]:
        async with self.connect() as db:
            cur = await db.execute("SELECT mode FROM user_modes WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            return row["mode"] if row else None

    # ---------- groups ----------
    async def create_group(self, title: str) -> int:
        async with self.connect() as db:
            cur = await db.execute("INSERT INTO groups(title) VALUES(?)", (title,))
            gid = cur.lastrowid
            await db.execute("INSERT OR IGNORE INTO group_settings(group_id) VALUES(?)", (gid,))
            await db.commit()
            return int(gid)

    async def list_groups(self, offset: int, limit: int) -> List[dict]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM groups WHERE is_active=1 ORDER BY group_id LIMIT ? OFFSET ?",
                (limit, offset)
            )
            return [dict(r) for r in rows]

    async def count_groups(self) -> int:
        async with self.connect() as db:
            cur = await db.execute("SELECT COUNT(*) AS c FROM groups WHERE is_active=1")
            row = await cur.fetchone()
            return int(row["c"])

    async def get_group(self, group_id: int) -> Optional[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM groups WHERE group_id=?", (group_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_group_schedule(self, group_id: int, file_id: str) -> None:
        async with self.connect() as db:
            await db.execute("UPDATE groups SET schedule_file_id=? WHERE group_id=?", (file_id, group_id))
            await db.commit()

    async def update_group_title(self, group_id: int, title: str) -> None:
        async with self.connect() as db:
            await db.execute("UPDATE groups SET title=? WHERE group_id=?", (title, group_id))
            await db.commit()

    async def get_group_settings(self, group_id: int) -> Optional[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM group_settings WHERE group_id=?", (group_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_group_settings(self, group_id: int, **fields: Any) -> None:
        if not fields:
            return
        keys = []
        vals = []
        for k, v in fields.items():
            keys.append(f"{k}=?")
            vals.append(v)
        vals.append(group_id)
        sql = f"UPDATE group_settings SET {', '.join(keys)} WHERE group_id=?"
        async with self.connect() as db:
            await db.execute(sql, tuple(vals))
            await db.commit()

    async def list_group_users(self, group_id: int, offset: int, limit: int) -> List[dict]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                "SELECT user_id, username, full_name FROM users WHERE group_id=? ORDER BY full_name LIMIT ? OFFSET ?",
                (group_id, limit, offset)
            )
            return [dict(r) for r in rows]

    async def list_users_with_notify(self, group_id: int) -> List[int]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                "SELECT user_id FROM users WHERE group_id=? AND notify_open=1",
                (group_id,),
            )
            return [int(r["user_id"]) for r in rows]

    async def count_group_users(self, group_id: int) -> int:
        async with self.connect() as db:
            cur = await db.execute("SELECT COUNT(*) AS c FROM users WHERE group_id=?", (group_id,))
            row = await cur.fetchone()
            return int(row["c"])

    # ---------- chats / group mapping ----------
    async def upsert_chat(self, chat_id: int, title: str, chat_type: str, is_admin: bool) -> None:
        async with self.connect() as db:
            await db.execute(
                """INSERT INTO chats(chat_id, title, chat_type, is_admin, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET
                  title=excluded.title,
                  chat_type=excluded.chat_type,
                  is_admin=excluded.is_admin,
                  updated_at=excluded.updated_at""",
                (chat_id, title, chat_type, 1 if is_admin else 0, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def list_admin_chats(self, offset: int, limit: int) -> List[dict]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM chats WHERE is_admin=1 ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return [dict(r) for r in rows]

    async def count_admin_chats(self) -> int:
        async with self.connect() as db:
            cur = await db.execute("SELECT COUNT(*) AS c FROM chats WHERE is_admin=1")
            row = await cur.fetchone()
            return int(row["c"])

    async def get_chat(self, chat_id: int) -> Optional[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_group_chat(self, group_id: int, chat_id: int) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT OR REPLACE INTO group_chats(group_id, chat_id, created_at) VALUES(?,?,?)",
                (group_id, chat_id, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def delete_group_chat(self, group_id: int) -> None:
        async with self.connect() as db:
            await db.execute("DELETE FROM group_chats WHERE group_id=?", (group_id,))
            await db.commit()

    async def get_group_chat(self, group_id: int) -> Optional[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM group_chats WHERE group_id=?", (group_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    # ---------- invites ----------
    async def create_invite(self, token: str, group_id: int, created_at: str) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT INTO invites(token, group_id, created_at, is_active) VALUES(?,?,?,1)",
                (token, group_id, created_at)
            )
            await db.commit()

    async def resolve_invite(self, token: str) -> Optional[int]:
        async with self.connect() as db:
            cur = await db.execute(
                "SELECT group_id FROM invites WHERE token=? AND is_active=1",
                (token,)
            )
            row = await cur.fetchone()
            return int(row["group_id"]) if row else None

    # ---------- training slots ----------
    async def create_slot(self, group_id: int, starts_at: str, capacity: int, note: Optional[str]) -> int:
        async with self.connect() as db:
            cur = await db.execute(
                "INSERT INTO training_slots(group_id, starts_at, capacity, base_capacity, note) VALUES(?,?,?,?,?)",
                (group_id, starts_at, capacity, capacity, note)
            )
            await db.commit()
            return int(cur.lastrowid)

    async def list_slots_for_group(self, group_id: int, from_iso: str, to_iso: str, limit: int=25) -> List[dict]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """SELECT * FROM training_slots
                WHERE group_id=? AND is_active=1 AND starts_at BETWEEN ? AND ?
                ORDER BY starts_at LIMIT ?""",
                (group_id, from_iso, to_iso, limit)
            )
            return [dict(r) for r in rows]

    async def list_active_slots(self, from_iso: str, to_iso: str, limit: int = 200) -> List[dict]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """SELECT * FROM training_slots
                WHERE is_active=1 AND starts_at BETWEEN ? AND ?
                ORDER BY starts_at LIMIT ?""",
                (from_iso, to_iso, limit),
            )
            return [dict(r) for r in rows]

    async def get_slot(self, slot_id: int) -> Optional[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM training_slots WHERE slot_id=?", (slot_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_slot_time_capacity(self, slot_id: int, starts_at: str, capacity: int) -> None:
        async with self.connect() as db:
            await db.execute(
                "UPDATE training_slots SET starts_at=?, capacity=? WHERE slot_id=?",
                (starts_at, capacity, slot_id),
            )
            await db.commit()

    async def cancel_slot_bookings(self, slot_id: int) -> None:
        async with self.connect() as db:
            await db.execute(
                "UPDATE bookings SET status='cancelled' WHERE entity_type='training' AND entity_id=? AND status='active'",
                (slot_id,),
            )
            await db.commit()

    async def add_slot_exception(self, slot_id: int, starts_on: str) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO slot_exceptions(slot_id, starts_on, created_at) VALUES(?,?,?)",
                (slot_id, starts_on, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def has_slot_exception(self, slot_id: int, starts_on: str) -> bool:
        async with self.connect() as db:
            cur = await db.execute(
                "SELECT 1 FROM slot_exceptions WHERE slot_id=? AND starts_on=?",
                (slot_id, starts_on),
            )
            row = await cur.fetchone()
            return bool(row)

    async def add_slot_capacity(self, slot_id: int, delta: int) -> None:
        async with self.connect() as db:
            await db.execute(
                "UPDATE training_slots SET capacity=capacity+? WHERE slot_id=?",
                (int(delta), slot_id),
            )
            await db.commit()

    # ---------- tournaments ----------
    async def create_tournament(
        self,
        title: str,
        starts_at: str,
        capacity: int,
        amount: Optional[int],
        description: Optional[str],
        close_mode: str = "at_start",
        close_minutes_before: Optional[int] = None,
        cancel_minutes_before: int = 360,
        waitlist_limit: int = 0,
    ) -> int:
        async with self.connect() as db:
            cur = await db.execute(
                """INSERT INTO tournaments(
                    title, starts_at, capacity, amount, description,
                    close_mode, close_minutes_before, cancel_minutes_before, waitlist_limit
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    title,
                    starts_at,
                    capacity,
                    amount,
                    description,
                    close_mode,
                    close_minutes_before,
                    cancel_minutes_before,
                    waitlist_limit,
                ),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def add_tournament_group(self, tournament_id: int, group_id: int) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO tournament_groups(tournament_id, group_id) VALUES(?,?)",
                (tournament_id, group_id),
            )
            await db.commit()

    async def list_tournaments_for_groups(
        self,
        group_ids: List[int],
        from_iso: str,
        to_iso: str,
        limit: int = 25,
    ) -> List[dict]:
        if not group_ids:
            return []
        placeholders = ",".join(["?"] * len(group_ids))
        sql = f"""
            SELECT DISTINCT t.*
            FROM tournaments t
            JOIN tournament_groups tg ON tg.tournament_id = t.tournament_id
            WHERE tg.group_id IN ({placeholders})
              AND t.is_active=1
              AND t.starts_at BETWEEN ? AND ?
            ORDER BY t.starts_at
            LIMIT ?
        """
        params = group_ids + [from_iso, to_iso, limit]
        async with self.connect() as db:
            rows = await db.execute_fetchall(sql, params)
            return [dict(r) for r in rows]

    async def list_tournaments(self, offset: int, limit: int) -> List[dict]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM tournaments WHERE is_active=1 ORDER BY starts_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return [dict(r) for r in rows]

    async def count_tournaments(self) -> int:
        async with self.connect() as db:
            cur = await db.execute("SELECT COUNT(*) AS c FROM tournaments WHERE is_active=1")
            row = await cur.fetchone()
            return int(row["c"])

    async def get_tournament(self, tournament_id: int) -> Optional[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM tournaments WHERE tournament_id=?", (tournament_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_tournament_groups(self, tournament_id: int) -> List[int]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                "SELECT group_id FROM tournament_groups WHERE tournament_id=?",
                (tournament_id,),
            )
            return [int(r["group_id"]) for r in rows]

    async def update_tournament_settings(self, tournament_id: int, **fields: Any) -> None:
        if not fields:
            return
        keys = []
        vals = []
        for k, v in fields.items():
            keys.append(f"{k}=?")
            vals.append(v)
        vals.append(tournament_id)
        sql = f"UPDATE tournaments SET {', '.join(keys)} WHERE tournament_id=?"
        async with self.connect() as db:
            await db.execute(sql, tuple(vals))
            await db.commit()

    # ---------- admins ----------
    async def add_admin(self, user_id: int) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO admins(user_id, created_at) VALUES (?, ?)",
                (user_id, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def is_admin(self, user_id: int) -> bool:
        async with self.connect() as db:
            cur = await db.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            return row is not None

    async def list_admins(self) -> List[int]:
        async with self.connect() as db:
            rows = await db.execute_fetchall("SELECT user_id FROM admins")
            return [int(r["user_id"]) for r in rows]

    async def create_admin_invite(self, token: str) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT INTO admin_invites(token, created_at, is_active) VALUES (?,?,1)",
                (token, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def resolve_admin_invite(self, token: str) -> bool:
        async with self.connect() as db:
            cur = await db.execute(
                "SELECT token FROM admin_invites WHERE token=? AND is_active=1",
                (token,),
            )
            row = await cur.fetchone()
            if not row:
                return False
            await db.execute(
                "UPDATE admin_invites SET is_active=0 WHERE token=?",
                (token,),
            )
            await db.commit()
            return True

    async def reset_all(self) -> None:
        async with self.connect() as db:
            await db.execute("DELETE FROM payments")
            await db.execute("DELETE FROM bookings")
            await db.execute("DELETE FROM tournament_groups")
            await db.execute("DELETE FROM tournaments")
            await db.execute("DELETE FROM training_slots")
            await db.execute("DELETE FROM invites")
            await db.execute("DELETE FROM group_settings")
            await db.execute("DELETE FROM groups")
            await db.execute("DELETE FROM users")
            await db.execute("DELETE FROM user_modes")
            await db.execute(
                "UPDATE payment_settings SET text=?, amount=?, updated_at=? WHERE id=1",
                ("Оплата: уточните у тренера.", None, datetime.utcnow().isoformat()),
            )
            await db.execute(
                "DELETE FROM sqlite_sequence WHERE name IN "
                "('groups','training_slots','tournaments','bookings','payments')"
            )
            await db.commit()

    async def count_active_bookings(self, entity_type: str, entity_id: int) -> int:
        async with self.connect() as db:
            cur = await db.execute(
                "SELECT COALESCE(SUM(seats),0) AS c FROM bookings WHERE entity_type=? AND entity_id=? AND status='active'",
                (entity_type, entity_id)
            )
            row = await cur.fetchone()
            return int(row["c"])

    async def list_active_booking_user_ids(self, entity_type: str, entity_id: int) -> List[int]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                "SELECT user_id FROM bookings WHERE entity_type=? AND entity_id=? AND status='active'",
                (entity_type, entity_id),
            )
            return [int(r["user_id"]) for r in rows]

    async def count_bookings(self, entity_type: str, entity_id: int, status: str) -> int:
        async with self.connect() as db:
            cur = await db.execute(
                "SELECT COUNT(*) AS c FROM bookings WHERE entity_type=? AND entity_id=? AND status=?",
                (entity_type, entity_id, status)
            )
            row = await cur.fetchone()
            return int(row["c"])

    async def get_user_booking(self, user_id: int, entity_type: str, entity_id: int) -> Optional[dict]:
        async with self.connect() as db:
            cur = await db.execute(
                "SELECT * FROM bookings WHERE user_id=? AND entity_type=? AND entity_id=? AND status='active'",
                (user_id, entity_type, entity_id)
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_user_booking_any(self, user_id: int, entity_type: str, entity_id: int) -> Optional[dict]:
        async with self.connect() as db:
            cur = await db.execute(
                "SELECT * FROM bookings WHERE user_id=? AND entity_type=? AND entity_id=? AND status IN ('active','waitlist')",
                (user_id, entity_type, entity_id)
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def create_booking(self, user_id: int, entity_type: str, entity_id: int, status: str = "active", seats: int = 1) -> int:
        async with self.connect() as db:
            cur = await db.execute(
                "INSERT INTO bookings(user_id, entity_type, entity_id, status, seats, created_at) VALUES(?,?,?,?,?,?)",
                (user_id, entity_type, entity_id, status, int(seats), datetime.utcnow().isoformat())
            )
            booking_id = int(cur.lastrowid)
            await db.execute(
                "INSERT OR IGNORE INTO payments(booking_id, status) VALUES(?, 'pending')",
                (booking_id,)
            )
            await db.commit()
            return booking_id

    async def update_booking_seats(self, booking_id: int, seats: int) -> None:
        async with self.connect() as db:
            await db.execute("UPDATE bookings SET seats=? WHERE booking_id=?", (int(seats), booking_id))
            await db.commit()

    async def cancel_booking(self, booking_id: int) -> None:
        async with self.connect() as db:
            await db.execute("UPDATE bookings SET status='cancelled' WHERE booking_id=?", (booking_id,))
            await db.commit()

    async def update_booking_status(self, booking_id: int, status: str) -> None:
        async with self.connect() as db:
            await db.execute("UPDATE bookings SET status=? WHERE booking_id=?", (status, booking_id))
            await db.commit()

    async def list_entity_bookings(self, entity_type: str, entity_id: int, offset: int, limit: int, status: str = "active") -> List[dict]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """SELECT b.booking_id, b.user_id, b.seats, u.full_name, u.username, p.status AS pay_status
                FROM bookings b
                JOIN users u ON u.user_id=b.user_id
                LEFT JOIN payments p ON p.booking_id=b.booking_id
                WHERE b.entity_type=? AND b.entity_id=? AND b.status=?
                ORDER BY b.created_at
                LIMIT ? OFFSET ?""",
                (entity_type, entity_id, status, limit, offset)
            )
            return [dict(r) for r in rows]

    async def count_entity_bookings(self, entity_type: str, entity_id: int, status: str = "active") -> int:
        async with self.connect() as db:
            cur = await db.execute(
                "SELECT COUNT(*) AS c FROM bookings WHERE entity_type=? AND entity_id=? AND status=?",
                (entity_type, entity_id, status)
            )
            row = await cur.fetchone()
            return int(row["c"])

    async def pop_waitlist(self, entity_type: str, entity_id: int) -> Optional[dict]:
        async with self.connect() as db:
            cur = await db.execute(
                "SELECT * FROM bookings WHERE entity_type=? AND entity_id=? AND status='waitlist' ORDER BY created_at LIMIT 1",
                (entity_type, entity_id),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    # ---------- notifications ----------
    async def list_notified_user_ids(self, slot_id: int) -> List[int]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                "SELECT user_id FROM notify_open_log WHERE slot_id=?",
                (slot_id,),
            )
            return [int(r["user_id"]) for r in rows]

    async def mark_open_notified(self, user_id: int, slot_id: int) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO notify_open_log(user_id, slot_id, sent_at) VALUES(?,?,?)",
                (user_id, slot_id, datetime.utcnow().isoformat()),
            )
            await db.commit()

    # ---------- full slot notifications ----------
    async def add_full_notification(self, slot_id: int, admin_id: int, message_id: int) -> None:
        async with self.connect() as db:
            await db.execute(
                """INSERT OR REPLACE INTO slot_full_notifications(slot_id, admin_id, message_id, created_at)
                VALUES(?,?,?,?)""",
                (slot_id, admin_id, message_id, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def list_full_notifications(self, slot_id: int) -> List[dict]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM slot_full_notifications WHERE slot_id=?",
                (slot_id,),
            )
            return [dict(r) for r in rows]

    async def clear_full_notifications(self, slot_id: int) -> None:
        async with self.connect() as db:
            await db.execute("DELETE FROM slot_full_notifications WHERE slot_id=?", (slot_id,))
            await db.commit()

    async def toggle_payment(self, booking_id: int, admin_id: int) -> str:
        async with self.connect() as db:
            cur = await db.execute("SELECT status FROM payments WHERE booking_id=?", (booking_id,))
            row = await cur.fetchone()
            if row is None:
                await db.execute("INSERT INTO payments(booking_id, status) VALUES(?, 'pending')", (booking_id,))
                status = 'pending'
            else:
                status = row["status"]
            new_status = 'confirmed' if status != 'confirmed' else 'pending'
            await db.execute(
                "UPDATE payments SET status=?, confirmed_by=?, confirmed_at=? WHERE booking_id=?",
                (new_status, admin_id, datetime.utcnow().isoformat(), booking_id)
            )
            await db.commit()
            return new_status

    # ---------- payment settings ----------
    async def get_payment_settings(self) -> dict:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM payment_settings WHERE id=1")
            row = await cur.fetchone()
            return dict(row)

    async def set_payment_settings(self, text: str, amount: Optional[int]) -> None:
        async with self.connect() as db:
            await db.execute(
                "UPDATE payment_settings SET text=?, amount=?, updated_at=? WHERE id=1",
                (text, amount, datetime.utcnow().isoformat())
            )
            await db.commit()

    # ---------- notify settings ----------
    async def get_notify_settings(self) -> dict:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM notify_settings WHERE id=1")
            row = await cur.fetchone()
            return dict(row) if row else {"text": "Открыта запись на тренировку."}

    async def set_notify_settings(self, text: str) -> None:
        async with self.connect() as db:
            await db.execute(
                "UPDATE notify_settings SET text=?, updated_at=? WHERE id=1",
                (text, datetime.utcnow().isoformat()),
            )
            await db.commit()
