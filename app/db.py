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
  note TEXT,
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tournaments (
  tournament_id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  starts_at TEXT NOT NULL,
  capacity INTEGER NOT NULL,
  description TEXT,
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
  status TEXT NOT NULL DEFAULT 'active', -- active | cancelled
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
  text TEXT NOT NULL DEFAULT ':   .',
  amount INTEGER,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_modes (
  user_id INTEGER PRIMARY KEY,
  mode TEXT
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
            # seed payment_settings row
            cur = await db.execute("SELECT id FROM payment_settings WHERE id=1")
            row = await cur.fetchone()
            if row is None:
                await db.execute(
                    "INSERT INTO payment_settings(id, text, amount, updated_at) VALUES (1, ?, ?, ?)",
                    (":   .", None, datetime.utcnow().isoformat())
                )
            await db.commit()

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

    async def set_user_group(self, user_id: int, group_id: int) -> None:
        async with self.connect() as db:
            await db.execute("UPDATE users SET group_id=? WHERE user_id=?", (group_id, user_id))
            await db.commit()

    async def get_user(self, user_id: int) -> Optional[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

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

    async def count_group_users(self, group_id: int) -> int:
        async with self.connect() as db:
            cur = await db.execute("SELECT COUNT(*) AS c FROM users WHERE group_id=?", (group_id,))
            row = await cur.fetchone()
            return int(row["c"])

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
                "INSERT INTO training_slots(group_id, starts_at, capacity, note) VALUES(?,?,?,?)",
                (group_id, starts_at, capacity, note)
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

    async def get_slot(self, slot_id: int) -> Optional[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM training_slots WHERE slot_id=?", (slot_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def count_active_bookings(self, entity_type: str, entity_id: int) -> int:
        async with self.connect() as db:
            cur = await db.execute(
                "SELECT COUNT(*) AS c FROM bookings WHERE entity_type=? AND entity_id=? AND status='active'",
                (entity_type, entity_id)
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

    async def create_booking(self, user_id: int, entity_type: str, entity_id: int) -> int:
        async with self.connect() as db:
            cur = await db.execute(
                "INSERT INTO bookings(user_id, entity_type, entity_id, status, created_at) VALUES(?,?,?,?,?)",
                (user_id, entity_type, entity_id, 'active', datetime.utcnow().isoformat())
            )
            booking_id = int(cur.lastrowid)
            await db.execute(
                "INSERT OR IGNORE INTO payments(booking_id, status) VALUES(?, 'pending')",
                (booking_id,)
            )
            await db.commit()
            return booking_id

    async def cancel_booking(self, booking_id: int) -> None:
        async with self.connect() as db:
            await db.execute("UPDATE bookings SET status='cancelled' WHERE booking_id=?", (booking_id,))
            await db.commit()

    async def list_entity_bookings(self, entity_type: str, entity_id: int, offset: int, limit: int) -> List[dict]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """SELECT b.booking_id, b.user_id, u.full_name, u.username, p.status AS pay_status
                FROM bookings b
                JOIN users u ON u.user_id=b.user_id
                LEFT JOIN payments p ON p.booking_id=b.booking_id
                WHERE b.entity_type=? AND b.entity_id=? AND b.status='active'
                ORDER BY b.created_at
                LIMIT ? OFFSET ?""",
                (entity_type, entity_id, limit, offset)
            )
            return [dict(r) for r in rows]

    async def count_entity_bookings(self, entity_type: str, entity_id: int) -> int:
        async with self.connect() as db:
            cur = await db.execute(
                "SELECT COUNT(*) AS c FROM bookings WHERE entity_type=? AND entity_id=? AND status='active'",
                (entity_type, entity_id)
            )
            row = await cur.fetchone()
            return int(row["c"])

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
