from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional

def tz_now(tz_offset_hours: int) -> datetime:
    return datetime.now(timezone(timedelta(hours=tz_offset_hours)))

def parse_dt(iso_str: str) -> datetime:
    return datetime.fromisoformat(iso_str)

def fmt_dt(dt: datetime) -> str:
    # e.g. 24.01 19:00
    return dt.strftime("%d.%m %H:%M")

def fmt_dt_with_weekday(dt: datetime) -> str:
    # e.g. Пн 24.01 19:00
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    wd = weekdays[dt.weekday()]
    return f"{wd} {dt.strftime('%d.%m %H:%M')}"

def compute_open_datetime(starts_at: datetime, open_days_before: int, open_time_hhmm: str) -> datetime:
    hh, mm = open_time_hhmm.split(":")
    base = (starts_at - timedelta(days=open_days_before)).astimezone(starts_at.tzinfo)
    return base.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)

def compute_close_datetime(starts_at: datetime, close_mode: str, close_minutes_before: Optional[int]) -> datetime:
    if close_mode == "minutes_before" and close_minutes_before is not None:
        return starts_at - timedelta(minutes=int(close_minutes_before))
    return starts_at

def compute_cancel_deadline(starts_at: datetime, cancel_minutes_before: int) -> datetime:
    return starts_at - timedelta(minutes=int(cancel_minutes_before))
