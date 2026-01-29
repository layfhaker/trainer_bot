import asyncio

import logging

import os
import shutil
import sqlite3

import secrets

from datetime import timedelta

from typing import Optional



from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command

from aiogram.types import CallbackQuery, Message

from dotenv import load_dotenv



from app.db import DB

from app.keyboards import (
    kb_main, kb_back, kb_admin_root, kb_pagination, kb_group_actions,
    kb_slot_actions, kb_admin_slots_root, kb_tour_actions,
    kb_admin_tournaments_root, kb_admin_entity_users
)
from app.utils import (

    tz_now, parse_dt, fmt_dt, fmt_dt_with_weekday,
    compute_open_datetime, compute_close_datetime, compute_cancel_deadline

)



load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

_env_db = os.getenv("DATABASE_PATH", "").strip()
if _env_db:
    DATABASE_PATH = _env_db
else:
    # Prefer host-mounted volume if present, otherwise repo data DB.
    if os.path.exists("/data/trainer_bot.db"):
        DATABASE_PATH = "/data/trainer_bot.db"
    elif os.path.exists("data/trainer_bot.db"):
        DATABASE_PATH = "data/trainer_bot.db"
    else:
        DATABASE_PATH = "trainer_bot.db"

TZ_OFFSET_HOURS = int(os.getenv("TZ_OFFSET_HOURS", "3").strip() or "3")

ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
PROXY_URL = os.getenv("PROXY_URL", "").strip()


if not BOT_TOKEN:

    raise RuntimeError("BOT_TOKEN is empty. Put your token into .env")



logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

logger = logging.getLogger("trainer_bot")



session = AiohttpSession(proxy=PROXY_URL) if PROXY_URL else None
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    session=session,
)
dp = Dispatcher()

router = Router()

dp.include_router(router)



db = DB(DATABASE_PATH)



ADMIN_DRAFTS = {}  # user_id -> dict
ADMIN_CACHE = set()



# ---------------- helpers ----------------

def is_admin(user_id: int) -> bool:

    return user_id in ADMIN_IDS or user_id in ADMIN_CACHE



def mention(full_name: str, username: Optional[str]) -> str:

    if username:

        return f"{full_name} (@{username})"

    return full_name


def next_weekday_datetime(weekday: int, time_str: str):

    """Return next datetime for given weekday (0=Mon) at HH:MM in local TZ."""

    from datetime import datetime

    now = tz_now(TZ_OFFSET_HOURS)

    hour, minute = map(int, time_str.split(":"))

    days_ahead = (weekday - now.weekday()) % 7

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if days_ahead == 0 and candidate <= now:

        days_ahead = 7

    target = candidate + timedelta(days=days_ahead)

    return target


def find_latest_backup(backup_dir: str) -> Optional[str]:
    if not os.path.isdir(backup_dir):
        return None
    candidates = []
    for name in os.listdir(backup_dir):
        if name.startswith("trainer_bot_") and name.endswith(".db"):
            path = os.path.join(backup_dir, name)
            if os.path.isfile(path):
                candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def is_default_db(path: str) -> bool:
    if not os.path.exists(path):
        return True
    if os.path.getsize(path) == 0:
        return True
    try:
        conn = sqlite3.connect(path)
        try:
            cur = conn.execute("SELECT COUNT(*) FROM users")
            row = cur.fetchone()
            users = int(row[0]) if row else 0
            return users == 0
        finally:
            conn.close()
    except Exception:
        return True


def restore_db_if_default(db_path: str, backup_dir: str) -> None:
    if not is_default_db(db_path):
        return
    latest = find_latest_backup(backup_dir)
    if not latest:
        return
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    shutil.copy2(latest, db_path)
    logger.info("DB restored from backup: %s", latest)


def make_daily_backup_name(backup_dir: str, now_dt) -> str:
    date_str = now_dt.strftime("%Y-%m-%d")
    return os.path.join(backup_dir, f"trainer_bot_{date_str}.db")




async def backup_loop(db_path: str, backup_dir: str, hour: int = 3, minute: int = 0) -> None:
    while True:
        now = tz_now(TZ_OFFSET_HOURS)
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run = next_run + timedelta(days=1)
        sleep_seconds = (next_run - now).total_seconds()
        await asyncio.sleep(max(5, sleep_seconds))
        try:
            if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
                os.makedirs(backup_dir, exist_ok=True)
                dst = make_daily_backup_name(backup_dir, tz_now(TZ_OFFSET_HOURS))
                shutil.copy2(db_path, dst)
                logger.info("Daily backup created: %s", dst)
        except Exception as exc:
            logger.exception("backup_loop error: %s", exc)





async def show_main(target: Message | CallbackQuery, user_id: int, text: Optional[str] = None):

    u = await db.get_user(user_id)

    gid = u.get("group_id") if u else None

    prefix = "Выберите действие:"

    if gid is None:

        prefix = "Вы не привязаны к группе. Попросите у тренера ссылку-приглашение."

    msg_text = text or prefix

    kb = kb_main(is_admin(user_id))

    if isinstance(target, CallbackQuery):
        msg = target.message
        if getattr(msg, "photo", None):
            try:
                await msg.delete()
            except Exception:
                pass
            await bot.send_message(user_id, msg_text, reply_markup=kb)
        else:
            await msg.edit_text(msg_text, reply_markup=kb)
        await target.answer()

    else:

        await target.answer(msg_text, reply_markup=kb)


async def send_open_notifications() -> None:
    now = tz_now(TZ_OFFSET_HOURS)
    from_iso = (now - timedelta(days=1)).isoformat()
    to_iso = (now + timedelta(days=30)).isoformat()
    slots = await db.list_active_slots(from_iso, to_iso, limit=300)
    notify_settings = await db.get_notify_settings()
    base_text = (notify_settings.get("text") or "Открыта запись на тренировку.").strip()
    for slot in slots:
        settings = await db.get_group_settings(slot["group_id"])
        if not settings:
            continue
        starts = parse_dt(slot["starts_at"])
        open_dt = compute_open_datetime(starts, settings["open_days_before"], settings["open_time"])
        close_dt = compute_close_datetime(starts, settings["close_mode"], settings.get("close_minutes_before"))
        # send only at the moment of opening (within 1 minute window)
        if not (open_dt <= now < open_dt + timedelta(minutes=1)):
            continue
        users = await db.list_users_with_notify(slot["group_id"])
        if not users:
            continue
        notified = set(await db.list_notified_user_ids(slot["slot_id"]))
        booked_users = set(await db.list_active_booking_user_ids("training", slot["slot_id"]))
        g = await db.get_group(slot["group_id"])
        g_title = g["title"] if g else f"#{slot['group_id']}"
        text = (
            f"{base_text}\n"
            f"Группа: <b>{g_title}</b>\n"
            f"Дата: <b>{fmt_dt_with_weekday(starts)}</b>\n\n"
            "Можно записаться прямо здесь."
        )
        kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=[
            [__import__("aiogram").types.InlineKeyboardButton(
                text="✅ Записаться",
                callback_data=f"train:join:{slot['slot_id']}",
            )],
            [__import__("aiogram").types.InlineKeyboardButton(
                text="📋 Открыть занятие",
                callback_data=f"train:open:{slot['slot_id']}",
            )],
        ])
        for uid in users:
            if uid in notified or uid in booked_users:
                continue
            try:
                await bot.send_message(uid, text, reply_markup=kb)
                await db.mark_open_notified(uid, slot["slot_id"])
            except Exception:
                pass


async def notify_open_loop() -> None:
    while True:
        try:
            await send_open_notifications()
        except Exception as exc:
            logger.exception("notify_open_loop error: %s", exc)
        await asyncio.sleep(60)



# ---------------- start ----------------

@router.message(CommandStart())

async def start_handler(message: Message):

    user = message.from_user

    await db.upsert_user(user.id, user.username or "", user.full_name or "")

    # deep link: /start g_<token> or /start a_<token>

    payload = (message.text or "").split(maxsplit=1)

    if len(payload) == 2 and payload[1].startswith("g_"):

        token = payload[1][2:]

        gid = await db.resolve_invite(token)

        if gid:

            await db.set_user_group(user.id, gid)

            g = await db.get_group(gid)

            await message.answer(f"Готово. Вы добавлены в группу: <b>{g['title']}</b>")

        else:

            await message.answer("Ссылка недействительна или отключена.")

    elif len(payload) == 2 and payload[1].startswith("a_"):
        token = payload[1][2:]
        if is_admin(user.id):
            await message.answer("Вы уже админ.")
        else:
            ok = await db.resolve_admin_invite(token)
            if ok:
                await db.add_admin(user.id)
                ADMIN_CACHE.add(user.id)
                await message.answer("Готово. Вы добавлены в админы.")
            else:
                await message.answer("Ссылка недействительна или отключена.")

    await show_main(message, user.id)



@router.message(Command("cancel"))

async def cancel_any(message: Message):

    await db.set_mode(message.from_user.id, None)

    ADMIN_DRAFTS.pop(message.from_user.id, None)

    await message.answer("Отменено.", reply_markup=kb_main(is_admin(message.from_user.id)))



# ---------------- callbacks: main ----------------

@router.callback_query(F.data == "main")

async def cb_main(call: CallbackQuery):

    await show_main(call, call.from_user.id)



# ---------------- payment info ----------------

@router.callback_query(F.data == "pay:info")
async def cb_pay_info(call: CallbackQuery):

    s = await db.get_payment_settings()

    text = s.get("text") or "\u041e\u043f\u043b\u0430\u0442\u0430: \u0443\u0442\u043e\u0447\u043d\u0438\u0442\u0435 \u0443 \u0442\u0440\u0435\u043d\u0435\u0440\u0430."
    amount = s.get("amount")
    if amount:
        text = f"{text}\n\n\u0421\u0443\u043c\u043c\u0430: <b>{amount}</b>"

    if "\\u" in text:
        try:
            text = text.encode("utf-8").decode("unicode_escape")
        except Exception:
            pass

    await call.message.edit_text(text, reply_markup=kb_back("main"))

    await call.answer()


# ---------------- user settings ----------------

async def build_user_settings_view(user_id: int):
    u = await db.get_user(user_id)
    enabled = bool(u and u.get("notify_open"))
    status = "включены" if enabled else "выключены"
    text = (
        "<b>Настройки</b>\n"
        f"Оповещения об открытии записи на тренировки: <b>{status}</b>"
    )
    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(
            text="🔔 Включить оповещения" if not enabled else "🔕 Выключить оповещения",
            callback_data="user:settings:notify_open:toggle",
        )],
        [__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="main")],
    ]
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    return text, kb


@router.callback_query(F.data == "user:settings")
async def cb_user_settings(call: CallbackQuery):
    text, kb = await build_user_settings_view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "user:settings:notify_open:toggle")
async def cb_user_settings_notify_open_toggle(call: CallbackQuery):
    u = await db.get_user(call.from_user.id)
    enabled = bool(u and u.get("notify_open"))
    await db.set_user_notify_open(call.from_user.id, not enabled)
    text, kb = await build_user_settings_view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Оповещения " + ("включены" if not enabled else "выключены"))
@router.callback_query(F.data == "sched:show")

async def cb_schedule(call: CallbackQuery):

    u = await db.get_user(call.from_user.id)

    gid = u.get("group_id") if u else None

    if not gid:

        await call.answer("Сначала нужно быть в группе.", show_alert=True)

        return

    g = await db.get_group(gid)

    file_id = g.get("schedule_file_id")

    if not file_id:

        await call.message.edit_text("Расписание ещё не загружено.", reply_markup=kb_back("main"))

        await call.answer()

        return

        await bot.send_photo(
        call.from_user.id,
        photo=file_id,
        caption=f"Расписание: <b>{g['title']}</b>",
        reply_markup=kb_back("main"),
    )
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()



# ---------------- trainings: list/open/join/leave ----------------

@router.callback_query(F.data == "train:list")

async def cb_train_list(call: CallbackQuery):

    u = await db.get_user(call.from_user.id)

    gid = u.get("group_id") if u else None

    if not gid:

        await call.answer("Сначала нужно быть в группе.", show_alert=True)

        return

    now = tz_now(TZ_OFFSET_HOURS)

    from_iso = (now - timedelta(days=1)).isoformat()

    to_iso = (now + timedelta(days=21)).isoformat()

    slots = await db.list_slots_for_group(gid, from_iso, to_iso, limit=30)

    if not slots:

        await call.message.edit_text("Пока нет доступных занятий.", reply_markup=kb_back("main"))

        await call.answer()

        return

    lines = ["<b>Ближайшие занятия</b>:"]

    # show as buttons list (first 10) by edit message with inline keyboard per slot

    rows=[]

    for s in slots[:12]:

        dt = parse_dt(s["starts_at"])

        rows.append([__import__("aiogram").types.InlineKeyboardButton(

            text=f"{fmt_dt_with_weekday(dt)} (лимит {s['capacity']})",

            callback_data=f"train:open:{s['slot_id']}"

        )])

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="main")])

    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text("\n".join(lines), reply_markup=kb)

    await call.answer()



@router.callback_query(F.data.startswith("train:open:"))

async def cb_train_open(call: CallbackQuery):

    slot_id = int(call.data.split(":")[-1])

    slot = await db.get_slot(slot_id)

    if not slot or not slot.get("is_active"):

        await call.answer("Слот не найден.", show_alert=True)

        return

    u = await db.get_user(call.from_user.id)

    if not u or u.get("group_id") != slot["group_id"]:

        await call.answer("Это занятие не вашей группы.", show_alert=True)

        return



    settings = await db.get_group_settings(slot["group_id"])

    starts = parse_dt(slot["starts_at"])

    open_dt = compute_open_datetime(starts, settings["open_days_before"], settings["open_time"])

    close_dt = compute_close_datetime(starts, settings["close_mode"], settings.get("close_minutes_before"))

    cancel_deadline = compute_cancel_deadline(starts, settings["cancel_minutes_before"])



    now = tz_now(TZ_OFFSET_HOURS)

    booked = await db.count_active_bookings("training", slot_id)

    my_booking = await db.get_user_booking(call.from_user.id, "training", slot_id)
    my_seats = int(my_booking.get("seats", 1)) if my_booking else 0

    can_join = (now >= open_dt) and (now < close_dt) and (booked < slot["capacity"]) and (my_booking is None)
    can_join_second = (
        (now >= open_dt)
        and (now < close_dt)
        and (booked < slot["capacity"])
        and (my_booking is not None)
        and (my_seats < 2)
    )
    can_leave = (my_booking is not None) and (now < cancel_deadline)



    text = (

        f"<b>Занятие</b>\n"

        f"🕒 {fmt_dt_with_weekday(starts)}\n"

        f"👥 Мест: {booked}/{slot['capacity']}\n"

    )

    if slot.get("note"):

        text += f"📝 {slot['note']}\n"

    if now < open_dt:

        text += f"\nЗапись откроется: <b>{fmt_dt(open_dt)}</b>"

    elif now >= close_dt:

        text += f"\nЗапись закрыта."

    if my_booking:
        seats_info = f" ({my_seats} чел.)" if my_seats > 1 else ""
        if now < cancel_deadline:
            text += f"\n\nВы записаны{seats_info}. Отмена возможна до <b>{fmt_dt(cancel_deadline)}</b>."
        else:
            text += f"\n\nВы записаны{seats_info}. Отмена уже недоступна."

    await call.message.edit_text(
        text,
        reply_markup=kb_slot_actions(
            slot_id,
            can_join,
            can_leave,
            can_join_second,
            can_admin_book=is_admin(call.from_user.id),
        ),
    )

    await call.answer()



@router.callback_query(F.data.startswith("train:join:"))

async def cb_train_join(call: CallbackQuery):

    slot_id = int(call.data.split(":")[-1])

    slot = await db.get_slot(slot_id)

    if not slot:

        await call.answer("Слот не найден.", show_alert=True)

        return

    u = await db.get_user(call.from_user.id)

    if not u or u.get("group_id") != slot["group_id"]:

        await call.answer("Это занятие не вашей группы.", show_alert=True)

        return

    settings = await db.get_group_settings(slot["group_id"])

    starts = parse_dt(slot["starts_at"])

    open_dt = compute_open_datetime(starts, settings["open_days_before"], settings["open_time"])

    close_dt = compute_close_datetime(starts, settings["close_mode"], settings.get("close_minutes_before"))

    now = tz_now(TZ_OFFSET_HOURS)

    if now < open_dt:

        await call.answer(f"Запись откроется {fmt_dt(open_dt)}", show_alert=True)

        return

    if now >= close_dt:

        await call.answer("Запись закрыта.", show_alert=True)

        return

    booked = await db.count_active_bookings("training", slot_id)

    if booked >= slot["capacity"]:

        await call.answer("Мест нет.", show_alert=True)

        return

    existing = await db.get_user_booking(call.from_user.id, "training", slot_id)

    if existing:

        await call.answer("Вы уже записаны.", show_alert=True)

        return

    await db.create_booking(call.from_user.id, "training", slot_id)

    await call.answer("Записал ✅")

    await cb_train_open(call)



@router.callback_query(F.data.startswith("train:join2:"))
async def cb_train_join_second(call: CallbackQuery):
    slot_id = int(call.data.split(":")[-1])
    slot = await db.get_slot(slot_id)
    if not slot:
        await call.answer("Слот не найден.", show_alert=True)
        return
    u = await db.get_user(call.from_user.id)
    if not u or u.get("group_id") != slot["group_id"]:
        await call.answer("Это занятие не вашей группы.", show_alert=True)
        return
    settings = await db.get_group_settings(slot["group_id"])
    starts = parse_dt(slot["starts_at"])
    open_dt = compute_open_datetime(starts, settings["open_days_before"], settings["open_time"])
    close_dt = compute_close_datetime(starts, settings["close_mode"], settings.get("close_minutes_before"))
    now = tz_now(TZ_OFFSET_HOURS)
    if now < open_dt:
        await call.answer(f"Запись откроется {fmt_dt(open_dt)}", show_alert=True)
        return
    if now >= close_dt:
        await call.answer("Запись закрыта.", show_alert=True)
        return

    booked = await db.count_active_bookings("training", slot_id)
    if booked >= slot["capacity"]:
        await call.answer("Мест нет.", show_alert=True)
        return

    existing = await db.get_user_booking(call.from_user.id, "training", slot_id)
    if not existing:
        await db.create_booking(call.from_user.id, "training", slot_id)
        await call.answer("Записал ✅")
        await cb_train_open(call)
        return

    current_seats = int(existing.get("seats", 1))
    if current_seats >= 2:
        await call.answer("Вы уже записали двоих.", show_alert=True)
        return

    await db.update_booking_seats(existing["booking_id"], current_seats + 1)
    await call.answer("Записал второго человека ✅")
    await cb_train_open(call)


@router.callback_query(F.data.startswith("train:leave:"))

async def cb_train_leave(call: CallbackQuery):

    slot_id = int(call.data.split(":")[-1])

    slot = await db.get_slot(slot_id)

    if not slot:

        await call.answer("Слот не найден.", show_alert=True)

        return

    settings = await db.get_group_settings(slot["group_id"])

    starts = parse_dt(slot["starts_at"])

    cancel_deadline = compute_cancel_deadline(starts, settings["cancel_minutes_before"])

    now = tz_now(TZ_OFFSET_HOURS)

    booking = await db.get_user_booking(call.from_user.id, "training", slot_id)

    if not booking:

        await call.answer("Вы не записаны.", show_alert=True)

        return

    if now >= cancel_deadline:

        await call.answer("Отмена уже недоступна.", show_alert=True)

        return

    seats = int(booking.get("seats", 1))
    if seats > 1:
        await db.update_booking_seats(booking["booking_id"], seats - 1)
        await call.answer("Убрали одного человека ❌")
    else:
        await db.cancel_booking(booking["booking_id"])
        await call.answer("Отменил ❌")

    await cb_train_open(call)


@router.callback_query(F.data.startswith("admin:training:book:"))
async def cb_admin_training_book(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    parts = call.data.split(":")
    slot_id = int(parts[3])
    back_mode = parts[4] if len(parts) > 4 else "admin"
    slot = await db.get_slot(slot_id)
    if not slot:
        await call.answer("Слот не найден.", show_alert=True)
        return
    await db.set_mode(call.from_user.id, f"admin_training_book:{slot_id}:{back_mode}")
    back_to = f"admin:slot:open:{slot_id}" if back_mode == "admin" else f"train:open:{slot_id}"
    await call.message.edit_text(
        "Введите имя человека для записи.\n"
        "Например: Иван Иванов\n"
        "/cancel — отмена.",
        reply_markup=kb_back(back_to),
    )
    await call.answer()



# ---------------- tournaments ----------------
@router.callback_query(F.data == "tour:list")
async def cb_tour_list(call: CallbackQuery):
    u = await db.get_user(call.from_user.id)
    gid = u.get("group_id") if u else None
    if not gid:
        await call.answer("Сначала нужно быть в группе.", show_alert=True)
        return
    now = tz_now(TZ_OFFSET_HOURS)
    from_iso = (now - timedelta(days=1)).isoformat()
    to_iso = (now + timedelta(days=30)).isoformat()
    tournaments = await db.list_tournaments_for_groups([gid], from_iso, to_iso, limit=30)
    if not tournaments:
        await call.message.edit_text("Пока нет доступных турниров.", reply_markup=kb_back("main"))
        await call.answer()
        return
    rows = []
    for t in tournaments[:12]:
        dt = parse_dt(t["starts_at"])
        rows.append([__import__("aiogram").types.InlineKeyboardButton(
            text=f"{fmt_dt(dt)} — {t['title']}",
            callback_data=f"tour:open:{t['tournament_id']}"
        )])
    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="main")])
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text("<b>Турниры</b>:", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("tour:open:"))
async def cb_tour_open(call: CallbackQuery):
    tournament_id = int(call.data.split(":")[-1])
    t = await db.get_tournament(tournament_id)
    if not t or not t.get("is_active"):
        await call.answer("Турнир не найден.", show_alert=True)
        return
    u = await db.get_user(call.from_user.id)
    gid = u.get("group_id") if u else None
    if not gid:
        await call.answer("Сначала нужно быть в группе.", show_alert=True)
        return
    groups = await db.list_tournament_groups(tournament_id)
    if gid not in groups:
        await call.answer("Этот турнир не для вашей группы.", show_alert=True)
        return

    starts = parse_dt(t["starts_at"])
    close_dt = compute_close_datetime(starts, t["close_mode"], t.get("close_minutes_before"))
    cancel_deadline = compute_cancel_deadline(starts, t["cancel_minutes_before"])
    now = tz_now(TZ_OFFSET_HOURS)

    booked = await db.count_active_bookings("tournament", tournament_id)
    waitlist_count = await db.count_bookings("tournament", tournament_id, "waitlist")
    my_booking = await db.get_user_booking_any(call.from_user.id, "tournament", tournament_id)
    my_active_booking = await db.get_user_booking(call.from_user.id, "tournament", tournament_id)
    my_seats = int(my_active_booking.get("seats", 1)) if my_active_booking else 0

    waitlist_limit = int(t.get("waitlist_limit") or 0)
    has_waitlist_spots = waitlist_limit > 0 and waitlist_count < waitlist_limit

    can_join = (now < close_dt) and (my_booking is None) and (booked < t["capacity"] or has_waitlist_spots)
    can_join_second = (
        (now < close_dt)
        and (my_active_booking is not None)
        and (my_seats < 2)
        and (booked < t["capacity"])
    )
    can_leave = (my_booking is not None) and (now < cancel_deadline)
    is_waitlist = my_booking is not None and my_booking.get("status") == "waitlist"

    text = (
        f"<b>{t['title']}</b>\n"
        f"🕒 {fmt_dt(starts)}\n"
        f"👥 Мест: {booked}/{t['capacity']}\n"
    )
    if waitlist_limit > 0:
        text += f"📋 Лист ожидания: {waitlist_count}/{waitlist_limit}\n"
    if t.get("description"):
        text += f"📝 {t['description']}\n"
    if now >= close_dt:
        text += "\nЗапись закрыта."
    if my_booking:
        if is_waitlist:
            text += "\n\nВы в листе ожидания."
        else:
            seats_info = f" ({my_seats} чел.)" if my_seats > 1 else ""
            text += f"\n\nВы записаны{seats_info}."
        if now < cancel_deadline:
            text += f" Отмена возможна до <b>{fmt_dt(cancel_deadline)}</b>."
        else:
            text += " Отмена уже недоступна."

    await call.message.edit_text(text, reply_markup=kb_tour_actions(tournament_id, can_join, can_leave, is_waitlist, can_join_second))
    await call.answer()

@router.callback_query(F.data.startswith("tour:join:"))
async def cb_tour_join(call: CallbackQuery):
    tournament_id = int(call.data.split(":")[-1])
    t = await db.get_tournament(tournament_id)
    if not t:
        await call.answer("Турнир не найден.", show_alert=True)
        return
    u = await db.get_user(call.from_user.id)
    gid = u.get("group_id") if u else None
    if not gid:
        await call.answer("Сначала нужно быть в группе.", show_alert=True)
        return
    groups = await db.list_tournament_groups(tournament_id)
    if gid not in groups:
        await call.answer("Этот турнир не для вашей группы.", show_alert=True)
        return
    starts = parse_dt(t["starts_at"])
    close_dt = compute_close_datetime(starts, t["close_mode"], t.get("close_minutes_before"))
    now = tz_now(TZ_OFFSET_HOURS)
    if now >= close_dt:
        await call.answer("Запись закрыта.", show_alert=True)
        return
    existing = await db.get_user_booking_any(call.from_user.id, "tournament", tournament_id)
    if existing:
        await call.answer("Вы уже записаны.", show_alert=True)
        return
    booked = await db.count_active_bookings("tournament", tournament_id)
    waitlist_count = await db.count_bookings("tournament", tournament_id, "waitlist")
    waitlist_limit = int(t.get("waitlist_limit") or 0)
    if booked < t["capacity"]:
        await db.create_booking(call.from_user.id, "tournament", tournament_id, status="active")
        await call.answer("Записал ?")
    elif waitlist_limit > 0 and waitlist_count < waitlist_limit:
        await db.create_booking(call.from_user.id, "tournament", tournament_id, status="waitlist")
        await call.answer("Добавил в лист ожидания ?")
    else:
        await call.answer("Мест нет.", show_alert=True)
        return
    await cb_tour_open(call)

@router.callback_query(F.data.startswith("tour:join2:"))
async def cb_tour_join_second(call: CallbackQuery):
    tournament_id = int(call.data.split(":")[-1])
    t = await db.get_tournament(tournament_id)
    if not t:
        await call.answer("Турнир не найден.", show_alert=True)
        return
    u = await db.get_user(call.from_user.id)
    gid = u.get("group_id") if u else None
    if not gid:
        await call.answer("Сначала нужно быть в группе.", show_alert=True)
        return
    groups = await db.list_tournament_groups(tournament_id)
    if gid not in groups:
        await call.answer("Этот турнир не для вашей группы.", show_alert=True)
        return
    starts = parse_dt(t["starts_at"])
    close_dt = compute_close_datetime(starts, t["close_mode"], t.get("close_minutes_before"))
    now = tz_now(TZ_OFFSET_HOURS)
    if now >= close_dt:
        await call.answer("Запись закрыта.", show_alert=True)
        return

    booked = await db.count_active_bookings("tournament", tournament_id)
    if booked >= t["capacity"]:
        await call.answer("Мест нет.", show_alert=True)
        return

    existing_active = await db.get_user_booking(call.from_user.id, "tournament", tournament_id)
    if not existing_active:
        await db.create_booking(call.from_user.id, "tournament", tournament_id, status="active")
        await call.answer("Записал ✅")
        await cb_tour_open(call)
        return

    current_seats = int(existing_active.get("seats", 1))
    if current_seats >= 2:
        await call.answer("Вы уже записали двоих.", show_alert=True)
        return

    await db.update_booking_seats(existing_active["booking_id"], current_seats + 1)
    await call.answer("Записал второго человека ✅")
    await cb_tour_open(call)

@router.callback_query(F.data.startswith("tour:leave:"))
async def cb_tour_leave(call: CallbackQuery):
    tournament_id = int(call.data.split(":")[-1])
    t = await db.get_tournament(tournament_id)
    if not t:
        await call.answer("Турнир не найден.", show_alert=True)
        return
    starts = parse_dt(t["starts_at"])
    cancel_deadline = compute_cancel_deadline(starts, t["cancel_minutes_before"])
    now = tz_now(TZ_OFFSET_HOURS)
    booking = await db.get_user_booking_any(call.from_user.id, "tournament", tournament_id)
    if not booking:
        await call.answer("Вы не записаны.", show_alert=True)
        return
    if now >= cancel_deadline:
        await call.answer("Отмена уже недоступна.", show_alert=True)
        return
    seats = int(booking.get("seats", 1))
    if booking.get("status") == "active" and seats > 1:
        await db.update_booking_seats(booking["booking_id"], seats - 1)
        await call.answer("Убрали одного человека ❌")
        await cb_tour_open(call)
        return

    await db.cancel_booking(booking["booking_id"])

    if booking.get("status") == "active":
        next_wait = await db.pop_waitlist("tournament", tournament_id)
        if next_wait:
            await db.update_booking_status(next_wait["booking_id"], "active")
            try:
                await bot.send_message(
                    next_wait["user_id"],
                    f"Вы переведены из листа ожидания в запись на турнир: <b>{t['title']}</b>.\n"
                    f"Дата: {fmt_dt(starts)}"
                )
            except Exception:
                pass

    await call.answer("Отменил ?")
    await cb_tour_open(call)
# ---------------- admin root ----------------

@router.callback_query(F.data == "admin:root")
async def cb_admin_root(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await call.message.edit_text("Админ меню:", reply_markup=kb_admin_root())
    await call.answer()

@router.callback_query(F.data == "admin:reset")
async def cb_admin_reset(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(text="✅ Да, сбросить всё", callback_data="admin:reset:confirm")],
        [__import__("aiogram").types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin:root")],
    ]
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text(
        "Вы уверены? Это удалит группы, турниры, слоты, записи, пользователей, инвайты и платежи.",
        reply_markup=kb,
    )
    await call.answer()

@router.callback_query(F.data == "admin:reset:confirm")
async def cb_admin_reset_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await db.reset_all()
    await call.message.edit_text("Сброс выполнен.", reply_markup=kb_admin_root())
    await call.answer()

@router.callback_query(F.data == "admin:invite_admin")
async def cb_admin_invite_admin(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    token = secrets.token_urlsafe(8)
    await db.create_admin_invite(token)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=a_{token}"
    await call.message.edit_text(
        "Ссылка для добавления администратора:\n"
        f"{link}\n\n"
        "Передайте эту ссылку человеку.",
        reply_markup=kb_back("admin:root"),
    )
    await call.answer()

@router.callback_query(F.data.startswith("admin:groups:page:"))

async def cb_admin_groups(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    page = int(call.data.split(":")[-1])

    limit = 8

    offset = page * limit

    total = await db.count_groups()

    groups = await db.list_groups(offset, limit)

    rows = []

    for g in groups:

        rows.append([__import__("aiogram").types.InlineKeyboardButton(

            text=f"{g['group_id']}. {g['title']}",

            callback_data=f"admin:group:{g['group_id']}"

        )])

    # create button + paging + back

    nav = []

    if page > 0:

        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="⬅️", callback_data=f"admin:groups:page:{page-1}"))

    if offset + limit < total:

        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="➡️", callback_data=f"admin:groups:page:{page+1}"))

    if nav:

        rows.append(nav)

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="➕ Создать группу", callback_data="admin:group:create")])

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:root")])

    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text("<b>Группы</b>:", reply_markup=kb)

    await call.answer()



@router.callback_query(F.data == "admin:group:create")

async def cb_admin_group_create(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    await db.set_mode(call.from_user.id, "admin_create_group:title")

    await call.message.edit_text("Введите название группы (сообщением).\n/cancel — отмена.", reply_markup=kb_back("admin:groups:page:0"))

    await call.answer()



@router.callback_query(F.data.regexp("^admin:group:\\d+$"))

async def cb_admin_group_open(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    group_id = int(call.data.split(":")[-1])

    g = await db.get_group(group_id)

    if not g:

        await call.answer("Группа не найдена.", show_alert=True)

        return

    await call.message.edit_text(

        f"<b>Группа</b>: {g['title']}\nID: {group_id}",

        reply_markup=kb_group_actions(group_id)

    )

    await call.answer()



@router.callback_query(F.data.regexp(r"^admin:group:\d+:title$"))
async def cb_admin_group_title(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    group_id = int(call.data.split(":")[2])
    await db.set_mode(call.from_user.id, f"admin_group_title:{group_id}")
    await call.message.edit_text("Введите новое название группы.\n/cancel — отмена.", reply_markup=kb_back(f"admin:group:{group_id}"))
    await call.answer()



@router.callback_query(F.data.startswith("admin:group:") & F.data.endswith(":sched"))

async def cb_admin_group_sched(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    group_id = int(call.data.split(":")[2])

    await db.set_mode(call.from_user.id, f"admin_group_sched:{group_id}")

    await call.message.edit_text("Пришлите картинку расписания (фото) для этой группы.\n/cancel — отмена.")

    await call.answer()



async def build_group_settings_view(group_id: int):
    s = await db.get_group_settings(group_id)
    close_text = (
        "\u0432 \u043c\u043e\u043c\u0435\u043d\u0442 \u043d\u0430\u0447\u0430\u043b\u0430"
        if s["close_mode"] == "at_start"
        else f"\u0437\u0430 {s.get('close_minutes_before')} \u043c\u0438\u043d."
    )
    text = (
        f"<b>\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u0433\u0440\u0443\u043f\u043f\u044b {group_id}</b>\n"
        f"\u041e\u0442\u043a\u0440\u044b\u0442\u0438\u0435 \u0437\u0430\u043f\u0438\u0441\u0438: \u0437\u0430 <b>{s['open_days_before']}</b> \u0434\u043d. \u0432 <b>{s['open_time']}</b>\n"
        f"\u041e\u0442\u043c\u0435\u043d\u0430 \u0437\u0430\u043f\u0438\u0441\u0438: \u0437\u0430 <b>{s['cancel_minutes_before']}</b> \u043c\u0438\u043d.\n"
        f"\u0417\u0430\u043a\u0440\u044b\u0442\u0438\u0435 \u0437\u0430\u043f\u0438\u0441\u0438: <b>{close_text}</b>"
    )

    rows = []
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="-1 \u0434\u0435\u043d\u044c", callback_data=f"admin:group:{group_id}:settings:open_days:dec"),
        __import__("aiogram").types.InlineKeyboardButton(text="+1 \u0434\u0435\u043d\u044c", callback_data=f"admin:group:{group_id}:settings:open_days:inc"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="\u0412\u0440\u0435\u043c\u044f \u043e\u0442\u043a\u0440\u044b\u0442\u0438\u044f", callback_data=f"admin:group:{group_id}:settings:open_time"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="-30 \u043c\u0438\u043d", callback_data=f"admin:group:{group_id}:settings:cancel_min:dec"),
        __import__("aiogram").types.InlineKeyboardButton(text="+30 \u043c\u0438\u043d", callback_data=f"admin:group:{group_id}:settings:cancel_min:inc"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u043e\u0442\u043c\u0435\u043d\u0443", callback_data=f"admin:group:{group_id}:settings:cancel_min"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="\u0417\u0430\u043a\u0440\u044b\u0442\u0438\u0435: \u043f\u0435\u0440\u0435\u043a\u043b\u044e\u0447\u0438\u0442\u044c", callback_data=f"admin:group:{group_id}:settings:close_mode:toggle"),
    ])
    if s["close_mode"] == "minutes_before":
        rows.append([
            __import__("aiogram").types.InlineKeyboardButton(text="-5 \u043c\u0438\u043d", callback_data=f"admin:group:{group_id}:settings:close_min:dec"),
            __import__("aiogram").types.InlineKeyboardButton(text="+5 \u043c\u0438\u043d", callback_data=f"admin:group:{group_id}:settings:close_min:inc"),
        ])
        rows.append([
            __import__("aiogram").types.InlineKeyboardButton(text="\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0437\u0430\u043a\u0440\u044b\u0442\u0438\u0435", callback_data=f"admin:group:{group_id}:settings:close_min"),
        ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="\u2b05\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data=f"admin:group:{group_id}"),
    ])
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    return text, kb

@router.callback_query(F.data.startswith("admin:group:") & F.data.endswith(":settings"))
async def cb_admin_group_settings(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нюет доступа.", show_alert=True)
        return
    group_id = int(call.data.split(":")[2])
    text, kb = await build_group_settings_view(group_id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:group:\d+:settings:open_days:(inc|dec)$"))
async def cb_admin_group_settings_open_days(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    parts = call.data.split(":")
    group_id = int(parts[2])
    action = parts[-1]
    s = await db.get_group_settings(group_id)
    val = int(s["open_days_before"])
    val = val + 1 if action == "inc" else max(0, val - 1)
    await db.update_group_settings(group_id, open_days_before=val)
    text, kb = await build_group_settings_view(group_id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:group:\d+:settings:cancel_min:(inc|dec)$"))
async def cb_admin_group_settings_cancel_min(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    parts = call.data.split(":")
    group_id = int(parts[2])
    action = parts[-1]
    s = await db.get_group_settings(group_id)
    val = int(s["cancel_minutes_before"])
    step = 30
    val = val + step if action == "inc" else max(0, val - step)
    await db.update_group_settings(group_id, cancel_minutes_before=val)
    text, kb = await build_group_settings_view(group_id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:group:\d+:settings:close_mode:toggle$"))
async def cb_admin_group_settings_close_mode(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    parts = call.data.split(":")
    group_id = int(parts[2])
    s = await db.get_group_settings(group_id)
    new_mode = "minutes_before" if s["close_mode"] == "at_start" else "at_start"
    updates = {"close_mode": new_mode}
    if new_mode == "minutes_before" and not s.get("close_minutes_before"):
        updates["close_minutes_before"] = 30
    await db.update_group_settings(group_id, **updates)
    text, kb = await build_group_settings_view(group_id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:group:\d+:settings:close_min:(inc|dec)$"))
async def cb_admin_group_settings_close_min(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    parts = call.data.split(":")
    group_id = int(parts[2])
    action = parts[-1]
    s = await db.get_group_settings(group_id)
    val = int(s.get("close_minutes_before") or 0)
    step = 5
    val = val + step if action == "inc" else max(0, val - step)
    await db.update_group_settings(group_id, close_minutes_before=val)
    text, kb = await build_group_settings_view(group_id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:group:\d+:settings:open_time$"))
async def cb_admin_group_settings_open_time(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    group_id = int(call.data.split(":")[2])
    await db.set_mode(call.from_user.id, f"admin_group_settings:open_time:{group_id}")
    await call.message.edit_text("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0432\u0440\u0435\u043c\u044f \u043e\u0442\u043a\u0440\u044b\u0442\u0438\u044f \u0432 \u0444\u043e\u0440\u043c\u0430\u0442\u0435 HH:MM.\n/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0430.", reply_markup=kb_back(f"admin:group:{group_id}:settings"))
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:group:\d+:settings:cancel_min$"))
async def cb_admin_group_settings_cancel_min_edit(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    group_id = int(call.data.split(":")[2])
    await db.set_mode(call.from_user.id, f"admin_group_settings:cancel_min:{group_id}")
    await call.message.edit_text("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0432\u0440\u0435\u043c\u044f \u043e\u0442\u043c\u0435\u043d\u044b (\u043c\u0438\u043d\u0443\u0442\u044b, \u0447\u0438\u0441\u043b\u043e).\n/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0430.", reply_markup=kb_back(f"admin:group:{group_id}:settings"))
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:group:\d+:settings:close_min$"))
async def cb_admin_group_settings_close_min_edit(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    group_id = int(call.data.split(":")[2])
    await db.set_mode(call.from_user.id, f"admin_group_settings:close_min:{group_id}")
    await call.message.edit_text("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0437\u0430\u043a\u0440\u044b\u0442\u0438\u0435 \u0437\u0430\u043f\u0438\u0441\u0438 (\u043c\u0438\u043d\u0443\u0442\u044b \u0434\u043e \u043d\u0430\u0447\u0430\u043b\u0430, \u0447\u0438\u0441\u043b\u043e).\n/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0430.", reply_markup=kb_back(f"admin:group:{group_id}:settings"))
    await call.answer()
@router.callback_query(F.data.startswith("admin:group:") & F.data.contains(":users:page:"))

async def cb_admin_group_users(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    parts = call.data.split(":")

    group_id = int(parts[2])

    page = int(parts[-1])

    limit = 15

    offset = page * limit

    total = await db.count_group_users(group_id)

    users = await db.list_group_users(group_id, offset, limit)

    lines=[f"<b>Ученики группы {group_id}</b> ({total}):"]

    for i, u in enumerate(users, start=offset+1):

        uname = f"@{u['username']}" if u.get("username") else ""

        lines.append(f"{i}) {u['full_name']} {uname}".strip())

    rows=[]

    nav=[]

    if page>0: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="⬅️", callback_data=f"admin:group:{group_id}:users:page:{page-1}"))

    if offset+limit<total: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="➡️", callback_data=f"admin:group:{group_id}:users:page:{page+1}"))

    if nav: rows.append(nav)

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:group:{group_id}")])

    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text("\n".join(lines), reply_markup=kb)

    await call.answer()



# ----------- admin: invites -----------
@router.callback_query(F.data == "admin:invites")
async def cb_admin_invites(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    total = await db.count_groups()
    if total == 0:
        rows = [
            [__import__("aiogram").types.InlineKeyboardButton(text="Создать группу", callback_data="admin:group:create")],
            [__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:root")],
        ]
        kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
        await call.message.edit_text("Групп ещё нет. Создайте группу.", reply_markup=kb)
        await call.answer()
        return
    await cb_admin_invite_pickgroup(call, page=0)

@router.callback_query(F.data.startswith("admin:invite:pickgroup:page:"))
async def cb_admin_invite_pickgroup_cb(call: CallbackQuery):
    page = int(call.data.split(":")[-1])
    await cb_admin_invite_pickgroup(call, page=page)

async def cb_admin_invite_pickgroup(call: CallbackQuery, page: int):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    limit = 8
    offset = page * limit
    total = await db.count_groups()
    groups = await db.list_groups(offset, limit)
    rows = []
    for g in groups:
        rows.append([__import__("aiogram").types.InlineKeyboardButton(
            text=f"{g['group_id']}. {g['title']}",
            callback_data=f"admin:invite:create:{g['group_id']}"
        )])
    nav = []
    if page > 0:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="⬅️", callback_data=f"admin:invite:pickgroup:page:{page-1}"))
    if offset + limit < total:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="➡️", callback_data=f"admin:invite:pickgroup:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:root")])
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text("Создание пригласительной ссылки. Выберите группу:", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("admin:invite:create:"))
async def cb_admin_invite_create(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    gid = int(call.data.split(":")[-1])
    g = await db.get_group(gid)
    if not g:
        await call.answer("Группа не найдена.", show_alert=True)
        return
    token = secrets.token_urlsafe(8)
    await db.create_invite(token, gid, tz_now(TZ_OFFSET_HOURS).isoformat())
    await call.message.edit_text(
        f"Ссылка для группы <b>{g['title']}</b>:\n"
        f"<code>https://t.me/{(await bot.me()).username}?start=g_{token}</code>",
        reply_markup=kb_back("admin:root"),
    )
    await call.answer()

# ----------- admin: slots root -----------# ----------- admin: slots root -----------

@router.callback_query(F.data == "admin:slots")

async def cb_admin_slots(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    await call.message.edit_text("Занятия (слоты):", reply_markup=kb_admin_slots_root())

    await call.answer()


# ----------- admin: tournaments root -----------
@router.callback_query(F.data == "admin:tournaments")
async def cb_admin_tournaments_root(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await call.message.edit_text("Турниры:", reply_markup=kb_admin_tournaments_root())
    await call.answer()

@router.callback_query(F.data == "admin:tournament:create")
async def cb_admin_tournament_create(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await call.message.edit_text("Выберите группу для турнира:", reply_markup=kb_back("admin:tournaments"))
    await cb_admin_tournament_pickgroup(call, page=0)

@router.callback_query(F.data.startswith("admin:tournament:pickgroup:page:"))
async def cb_admin_tournament_pickgroup_cb(call: CallbackQuery):
    page = int(call.data.split(":")[-1])
    await cb_admin_tournament_pickgroup(call, page)

async def cb_admin_tournament_pickgroup(call: CallbackQuery, page: int):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    limit = 8
    offset = page * limit
    total = await db.count_groups()
    groups = await db.list_groups(offset, limit)
    rows = []
    for g in groups:
        rows.append([__import__("aiogram").types.InlineKeyboardButton(
            text=f"{g['group_id']}. {g['title']}",
            callback_data=f"admin:tournament:create:group:{g['group_id']}"
        )])
    nav = []
    if page > 0:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="⬅️", callback_data=f"admin:tournament:pickgroup:page:{page-1}"))
    if offset + limit < total:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="➡️", callback_data=f"admin:tournament:pickgroup:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:tournaments")])
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text("Выберите группу для турнира:", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("admin:tournament:create:group:"))
async def cb_admin_tournament_create_group(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    group_id = int(call.data.split(":")[-1])
    g = await db.get_group(group_id)
    if not g:
        await call.answer("Группа не найдена.", show_alert=True)
        return
    draft = ADMIN_DRAFTS.setdefault(call.from_user.id, {"type": "tournament"})
    draft["group_id"] = group_id
    await db.set_mode(call.from_user.id, "admin_tournament_create:title")
    await call.message.edit_text(
        f"Создание турнира для группы <b>{g['title']}</b>.\n"
        "Шаг 1/5: отправьте название турнира.\n"
        "/cancel — отмена."
    )
    await call.answer()

@router.callback_query(F.data.startswith("admin:tournament:list:page:"))
async def cb_admin_tournament_list(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    page = int(call.data.split(":")[-1])
    limit = 10
    offset = page * limit
    total = await db.count_tournaments()
    tournaments = await db.list_tournaments(offset, limit)
    if not tournaments:
        await call.message.edit_text("Турниров пока нет.", reply_markup=kb_back("admin:tournaments"))
        await call.answer()
        return
    rows = []
    for t in tournaments:
        dt = parse_dt(t["starts_at"])
        rows.append([__import__("aiogram").types.InlineKeyboardButton(
            text=f"{t['tournament_id']}. {t['title']} — {fmt_dt(dt)}",
            callback_data=f"admin:tournament:open:{t['tournament_id']}"
        )])
    nav = []
    if page > 0:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="⬅️", callback_data=f"admin:tournament:list:page:{page-1}"))
    if offset + limit < total:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="➡️", callback_data=f"admin:tournament:list:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:tournaments")])
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text("Турниры:", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("admin:tournament:open:"))
async def cb_admin_tournament_open(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    tournament_id = int(call.data.split(":")[-1])
    t = await db.get_tournament(tournament_id)
    if not t:
        await call.answer("Турнир не найден.", show_alert=True)
        return
    starts = parse_dt(t["starts_at"])
    booked = await db.count_active_bookings("tournament", tournament_id)
    waitlist_count = await db.count_bookings("tournament", tournament_id, "waitlist")
    text = (
        f"<b>Турнир</b> #{tournament_id}\n"
        f"Название: {t['title']}\n"
        f"🕒 {fmt_dt(starts)}\n"
        f"👥 Мест: {booked}/{t['capacity']}\n"
        f"📋 Лист ожидания: {waitlist_count}/{t.get('waitlist_limit', 0)}\n"
    )
    if t.get("description"):
        text += f"\n📝 {t['description']}"
    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(text="👥 Записанные", callback_data=f"admin:tournament:{tournament_id}:users:page:0")],
        [__import__("aiogram").types.InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"admin:tournament:{tournament_id}:settings")],
        [__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:tournament:list:page:0")],
    ]
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("admin:tournament:") & F.data.contains(":users:page:"))
async def cb_admin_tournament_users(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    parts = call.data.split(":")
    tournament_id = int(parts[2])
    page = int(parts[-1])
    limit = 15
    offset = page * limit
    total = await db.count_entity_bookings("tournament", tournament_id, status="active")
    items = await db.list_entity_bookings("tournament", tournament_id, offset, limit, status="active")
    lines = [f"<b>\u0417\u0430\u043f\u0438\u0441\u0430\u043d\u043d\u044b\u0435 (\u0442\u0443\u0440\u043d\u0438\u0440 #{tournament_id})</b> ({total}):"]
    rows = []
    for i, it in enumerate(items, start=offset+1):
        uname = f"@{it['username']}" if it.get('username') else ""
        st = "\u2705" if it.get("pay_status") == "confirmed" else "\u23f3"
        seats = int(it.get("seats", 1))
        seat_suffix = f" x{seats}" if seats > 1 else ""
        lines.append(f"{i}) {it['full_name']} {uname}{seat_suffix} \u2014 {st}".strip())
        rows.append([__import__("aiogram").types.InlineKeyboardButton(
            text=f"{st} {it['full_name']}{seat_suffix}",
            callback_data=f"admin:pay:tournament:toggle:{it['booking_id']}:{tournament_id}:{page}"
        )])
    nav = []
    if page > 0:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="\u2b05\ufe0f", callback_data=f"admin:tournament:{tournament_id}:users:page:{page-1}"))
    if offset + limit < total:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="\u27a1\ufe0f", callback_data=f"admin:tournament:{tournament_id}:users:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="\u2b05\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data=f"admin:tournament:open:{tournament_id}")])
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text("\n".join(lines), reply_markup=kb)

    await call.answer()

async def build_tournament_settings_view(tournament_id: int):
    t = await db.get_tournament(tournament_id)
    if not t:
        return None, None
    starts = parse_dt(t["starts_at"])
    close_text = (
        "\u0432 \u043c\u043e\u043c\u0435\u043d\u0442 \u043d\u0430\u0447\u0430\u043b\u0430"
        if t["close_mode"] == "at_start"
        else f"\u0437\u0430 {t.get('close_minutes_before')} \u043c\u0438\u043d."
    )
    amount_val = t.get("amount")
    amount_text = amount_val if amount_val is not None else "\u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u0430"
    text_out = (
        f"<b>\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u0442\u0443\u0440\u043d\u0438\u0440\u0430 {tournament_id}</b>\n"
        f"\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435: {t['title']}\n"
        f"\u0414\u0430\u0442\u0430: {fmt_dt(starts)}\n"
        f"\u041c\u0435\u0441\u0442: {t['capacity']}\n"
        f"\u041b\u0438\u0441\u0442 \u043e\u0436\u0438\u0434\u0430\u043d\u0438\u044f: {t.get('waitlist_limit', 0)}\n"
        f"\u0421\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c: {amount_text}\n"
        f"\u0417\u0430\u043a\u0440\u044b\u0442\u0438\u0435 \u0437\u0430\u043f\u0438\u0441\u0438: {close_text}\n"
        f"\u041e\u0442\u043c\u0435\u043d\u0430: \u0437\u0430 {t['cancel_minutes_before']} \u043c\u0438\u043d."
    )

    rows = []
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435", callback_data=f"admin:tournament:{tournament_id}:settings:title"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0434\u0430\u0442\u0443", callback_data=f"admin:tournament:{tournament_id}:settings:starts_at"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="-1 \u043c\u0435\u0441\u0442\u043e", callback_data=f"admin:tournament:{tournament_id}:settings:capacity:dec"),
        __import__("aiogram").types.InlineKeyboardButton(text="+1 \u043c\u0435\u0441\u0442\u043e", callback_data=f"admin:tournament:{tournament_id}:settings:capacity:inc"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u043c\u0435\u0441\u0442\u0430", callback_data=f"admin:tournament:{tournament_id}:settings:capacity"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="-1 \u043b\u0438\u0441\u0442", callback_data=f"admin:tournament:{tournament_id}:settings:waitlist:dec"),
        __import__("aiogram").types.InlineKeyboardButton(text="+1 \u043b\u0438\u0441\u0442", callback_data=f"admin:tournament:{tournament_id}:settings:waitlist:inc"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u043b\u0438\u0441\u0442", callback_data=f"admin:tournament:{tournament_id}:settings:waitlist"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="-100 \u20bd", callback_data=f"admin:tournament:{tournament_id}:settings:amount:dec"),
        __import__("aiogram").types.InlineKeyboardButton(text="+100 \u20bd", callback_data=f"admin:tournament:{tournament_id}:settings:amount:inc"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0441\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c", callback_data=f"admin:tournament:{tournament_id}:settings:amount"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="\u0417\u0430\u043a\u0440\u044b\u0442\u0438\u0435: \u043f\u0435\u0440\u0435\u043a\u043b\u044e\u0447\u0438\u0442\u044c", callback_data=f"admin:tournament:{tournament_id}:settings:close_mode:toggle"),
    ])
    if t["close_mode"] == "minutes_before":
        rows.append([
            __import__("aiogram").types.InlineKeyboardButton(text="-5 \u043c\u0438\u043d", callback_data=f"admin:tournament:{tournament_id}:settings:close_min:dec"),
            __import__("aiogram").types.InlineKeyboardButton(text="+5 \u043c\u0438\u043d", callback_data=f"admin:tournament:{tournament_id}:settings:close_min:inc"),
        ])
        rows.append([
            __import__("aiogram").types.InlineKeyboardButton(text="\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0437\u0430\u043a\u0440\u044b\u0442\u0438\u0435", callback_data=f"admin:tournament:{tournament_id}:settings:close_min"),
        ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="-30 \u043c\u0438\u043d", callback_data=f"admin:tournament:{tournament_id}:settings:cancel_min:dec"),
        __import__("aiogram").types.InlineKeyboardButton(text="+30 \u043c\u0438\u043d", callback_data=f"admin:tournament:{tournament_id}:settings:cancel_min:inc"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u043e\u0442\u043c\u0435\u043d\u0443", callback_data=f"admin:tournament:{tournament_id}:settings:cancel_min"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435", callback_data=f"admin:tournament:{tournament_id}:settings:description"),
    ])
    rows.append([
        __import__("aiogram").types.InlineKeyboardButton(text="\u2b05\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data=f"admin:tournament:open:{tournament_id}"),
    ])
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    return text_out, kb

@router.callback_query(F.data.startswith("admin:tournament:") & F.data.endswith(":settings"))
async def cb_admin_tournament_settings(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    tournament_id = int(call.data.split(":")[2])
    text_out, kb = await build_tournament_settings_view(tournament_id)
    if not text_out:
        await call.answer("\u0422\u0443\u0440\u043d\u0438\u0440 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d.", show_alert=True)
        return
    await call.message.edit_text(text_out, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:title$"))
async def cb_admin_tournament_settings_title(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    tournament_id = int(call.data.split(":")[2])
    await db.set_mode(call.from_user.id, f"admin_tournament_settings:title:{tournament_id}")
    await call.message.edit_text("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0442\u0443\u0440\u043d\u0438\u0440\u0430.\n/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0430.", reply_markup=kb_back(f"admin:tournament:{tournament_id}:settings"))
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:starts_at$"))
async def cb_admin_tournament_settings_starts_at(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    tournament_id = int(call.data.split(":")[2])
    await db.set_mode(call.from_user.id, f"admin_tournament_settings:starts_at:{tournament_id}")
    await call.message.edit_text("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0434\u0430\u0442\u0443/\u0432\u0440\u0435\u043c\u044f \u0432 \u0444\u043e\u0440\u043c\u0430\u0442\u0435 YYYY-MM-DD HH:MM.\n/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0430.", reply_markup=kb_back(f"admin:tournament:{tournament_id}:settings"))
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:capacity:(inc|dec)$"))
async def cb_admin_tournament_settings_capacity_delta(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    parts = call.data.split(":")
    tournament_id = int(parts[2])
    action = parts[-1]
    t = await db.get_tournament(tournament_id)
    val = int(t["capacity"])
    val = val + 1 if action == "inc" else max(1, val - 1)
    await db.update_tournament_settings(tournament_id, capacity=val)
    text_out, kb = await build_tournament_settings_view(tournament_id)
    await call.message.edit_text(text_out, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:capacity$"))
async def cb_admin_tournament_settings_capacity(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    tournament_id = int(call.data.split(":")[2])
    await db.set_mode(call.from_user.id, f"admin_tournament_settings:capacity:{tournament_id}")
    await call.message.edit_text("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043b\u0438\u043c\u0438\u0442 \u043c\u0435\u0441\u0442 (\u0447\u0438\u0441\u043b\u043e).\n/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0430.", reply_markup=kb_back(f"admin:tournament:{tournament_id}:settings"))
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:waitlist:(inc|dec)$"))
async def cb_admin_tournament_settings_waitlist_delta(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    parts = call.data.split(":")
    tournament_id = int(parts[2])
    action = parts[-1]
    t = await db.get_tournament(tournament_id)
    val = int(t.get("waitlist_limit") or 0)
    val = val + 1 if action == "inc" else max(0, val - 1)
    await db.update_tournament_settings(tournament_id, waitlist_limit=val)
    text_out, kb = await build_tournament_settings_view(tournament_id)
    await call.message.edit_text(text_out, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:amount:(inc|dec)$"))
async def cb_admin_tournament_settings_amount_delta(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    parts = call.data.split(":")
    tournament_id = int(parts[2])
    action = parts[-1]
    t = await db.get_tournament(tournament_id)
    val = int(t.get("amount") or 0)
    step = 100
    val = val + step if action == "inc" else max(0, val - step)
    await db.update_tournament_settings(tournament_id, amount=val)
    text_out, kb = await build_tournament_settings_view(tournament_id)
    await call.message.edit_text(text_out, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:amount$"))
async def cb_admin_tournament_settings_amount(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    tournament_id = int(call.data.split(":")[2])
    await db.set_mode(call.from_user.id, f"admin_tournament_settings:amount:{tournament_id}")
    await call.message.edit_text("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0441\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c (\u0447\u0438\u0441\u043b\u043e, \u0432 \u0440\u0443\u0431\u043b\u044f\u0445).\n/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0430.", reply_markup=kb_back(f"admin:tournament:{tournament_id}:settings"))
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:waitlist$"))
async def cb_admin_tournament_settings_waitlist(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    tournament_id = int(call.data.split(":")[2])
    await db.set_mode(call.from_user.id, f"admin_tournament_settings:waitlist:{tournament_id}")
    await call.message.edit_text("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043b\u0438\u043c\u0438\u0442 \u043b\u0438\u0441\u0442\u0430 \u043e\u0436\u0438\u0434\u0430\u043d\u0438\u044f (\u0447\u0438\u0441\u043b\u043e, 0 = \u0431\u0435\u0437 \u043b\u0438\u0441\u0442\u0430).\n/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0430.", reply_markup=kb_back(f"admin:tournament:{tournament_id}:settings"))
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:close_mode:toggle$"))
async def cb_admin_tournament_settings_close_mode(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    tournament_id = int(call.data.split(":")[2])
    t = await db.get_tournament(tournament_id)
    new_mode = "minutes_before" if t["close_mode"] == "at_start" else "at_start"
    updates = {"close_mode": new_mode}
    if new_mode == "minutes_before" and not t.get("close_minutes_before"):
        updates["close_minutes_before"] = 30
    await db.update_tournament_settings(tournament_id, **updates)
    text_out, kb = await build_tournament_settings_view(tournament_id)
    await call.message.edit_text(text_out, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:close_min:(inc|dec)$"))
async def cb_admin_tournament_settings_close_min_delta(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    parts = call.data.split(":")
    tournament_id = int(parts[2])
    action = parts[-1]
    t = await db.get_tournament(tournament_id)
    val = int(t.get("close_minutes_before") or 0)
    step = 5
    val = val + step if action == "inc" else max(0, val - step)
    await db.update_tournament_settings(tournament_id, close_minutes_before=val)
    text_out, kb = await build_tournament_settings_view(tournament_id)
    await call.message.edit_text(text_out, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:close_min$"))
async def cb_admin_tournament_settings_close_min(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    tournament_id = int(call.data.split(":")[2])
    await db.set_mode(call.from_user.id, f"admin_tournament_settings:close_min:{tournament_id}")
    await call.message.edit_text("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0437\u0430\u043a\u0440\u044b\u0442\u0438\u0435 \u0437\u0430\u043f\u0438\u0441\u0438 (\u043c\u0438\u043d\u0443\u0442\u044b \u0434\u043e \u043d\u0430\u0447\u0430\u043b\u0430, \u0447\u0438\u0441\u043b\u043e).\n/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0430.", reply_markup=kb_back(f"admin:tournament:{tournament_id}:settings"))
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:cancel_min:(inc|dec)$"))
async def cb_admin_tournament_settings_cancel_min_delta(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    parts = call.data.split(":")
    tournament_id = int(parts[2])
    action = parts[-1]
    t = await db.get_tournament(tournament_id)
    val = int(t.get("cancel_minutes_before") or 0)
    step = 30
    val = val + step if action == "inc" else max(0, val - step)
    await db.update_tournament_settings(tournament_id, cancel_minutes_before=val)
    text_out, kb = await build_tournament_settings_view(tournament_id)
    await call.message.edit_text(text_out, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:cancel_min$"))
async def cb_admin_tournament_settings_cancel_min(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    tournament_id = int(call.data.split(":")[2])
    await db.set_mode(call.from_user.id, f"admin_tournament_settings:cancel_min:{tournament_id}")
    await call.message.edit_text("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043e\u0442\u043c\u0435\u043d\u0443 (\u043c\u0438\u043d\u0443\u0442\u044b, \u0447\u0438\u0441\u043b\u043e).\n/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0430.", reply_markup=kb_back(f"admin:tournament:{tournament_id}:settings"))
    await call.answer()

@router.callback_query(F.data.regexp(r"^admin:tournament:\d+:settings:description$"))
async def cb_admin_tournament_settings_description(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    tournament_id = int(call.data.split(":")[2])
    await db.set_mode(call.from_user.id, f"admin_tournament_settings:description:{tournament_id}")
    await call.message.edit_text("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u0438\u043b\u0438 '-' \u0447\u0442\u043e\u0431\u044b \u043e\u0447\u0438\u0441\u0442\u0438\u0442\u044c.\n/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0430.", reply_markup=kb_back(f"admin:tournament:{tournament_id}:settings"))
    await call.answer()

@router.callback_query(F.data == "admin:slot:create")
@router.callback_query(F.data == "admin:slot:create")

async def cb_admin_slot_create(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    await cb_admin_slot_create_pickgroup(call, page=0)
    await call.answer()


@router.callback_query(F.data.startswith("admin:slot:create:pickgroup:page:"))

async def cb_admin_slot_create_pickgroup_page(call: CallbackQuery):

    page = int(call.data.split(":")[-1])

    await cb_admin_slot_create_pickgroup(call, page)


async def cb_admin_slot_create_pickgroup(call: CallbackQuery, page: int):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    limit = 8

    offset = page * limit

    total = await db.count_groups()

    groups = await db.list_groups(offset, limit)

    rows = []

    for g in groups:

        rows.append([__import__("aiogram").types.InlineKeyboardButton(

            text=f"{g['group_id']}. {g['title']}",

            callback_data=f"admin:slot:create:group:{g['group_id']}"

        )])

    nav = []

    if page > 0:

        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="⬅️", callback_data=f"admin:slot:create:pickgroup:page:{page-1}"))

    if offset + limit < total:

        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="➡️", callback_data=f"admin:slot:create:pickgroup:page:{page+1}"))

    if nav:

        rows.append(nav)

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:slots")])

    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text("Создание слота: выберите группу.", reply_markup=kb)

    await call.answer()


@router.callback_query(F.data.startswith("admin:slot:create:group:"))

async def cb_admin_slot_create_group(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    group_id = int(call.data.split(":")[-1])

    g = await db.get_group(group_id)

    if not g:

        await call.answer("Группа не найдена.", show_alert=True)

        return

    draft = ADMIN_DRAFTS.setdefault(call.from_user.id, {"type": "slot"})

    draft["group_id"] = group_id

    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(text="Пн", callback_data="admin:slot:create:weekday:0")],
        [__import__("aiogram").types.InlineKeyboardButton(text="Вт", callback_data="admin:slot:create:weekday:1")],
        [__import__("aiogram").types.InlineKeyboardButton(text="Ср", callback_data="admin:slot:create:weekday:2")],
        [__import__("aiogram").types.InlineKeyboardButton(text="Чт", callback_data="admin:slot:create:weekday:3")],
        [__import__("aiogram").types.InlineKeyboardButton(text="Пт", callback_data="admin:slot:create:weekday:4")],
        [__import__("aiogram").types.InlineKeyboardButton(text="Сб", callback_data="admin:slot:create:weekday:5")],
        [__import__("aiogram").types.InlineKeyboardButton(text="Вс", callback_data="admin:slot:create:weekday:6")],
        [__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:slot:create:pickgroup:page:0")],
    ]

    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text(f"Создание слота для группы <b>{g['title']}</b>.\nВыберите день недели:", reply_markup=kb)

    await call.answer()


@router.callback_query(F.data.startswith("admin:slot:create:weekday:"))

async def cb_admin_slot_create_weekday(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    weekday = int(call.data.split(":")[-1])

    draft = ADMIN_DRAFTS.setdefault(call.from_user.id, {"type": "slot"})

    draft["weekday"] = weekday

    await db.set_mode(call.from_user.id, "admin_slot_create:time")

    await call.message.edit_text(
        "Шаг 2/3: отправьте время в формате HH:MM (например 19:00).\n"
        "/cancel — отмена."
    )

    await call.answer()



@router.callback_query(F.data.startswith("admin:slot:pickgroup:page:"))

async def cb_admin_pickgroup(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    page = int(call.data.split(":")[-1])

    limit=8

    offset=page*limit

    total=await db.count_groups()

    groups=await db.list_groups(offset, limit)

    rows=[]

    for g in groups:

        rows.append([__import__("aiogram").types.InlineKeyboardButton(

            text=f"{g['group_id']}. {g['title']}",

            callback_data=f"admin:slot:list:{g['group_id']}"

        )])

    nav=[]

    if page>0: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="⬅️", callback_data=f"admin:slot:pickgroup:page:{page-1}"))

    if offset+limit<total: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="➡️", callback_data=f"admin:slot:pickgroup:page:{page+1}"))

    if nav: rows.append(nav)

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:slots")])

    kb=__import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text("Выберите группу:", reply_markup=kb)

    await call.answer()



@router.callback_query(F.data.startswith("admin:slot:list:"))

async def cb_admin_slot_list_for_group(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    gid=int(call.data.split(":")[-1])

    now=tz_now(TZ_OFFSET_HOURS)

    from_iso=(now - timedelta(days=1)).isoformat()

    to_iso=(now + timedelta(days=30)).isoformat()

    slots=await db.list_slots_for_group(gid, from_iso, to_iso, limit=30)

    if not slots:

        await call.message.edit_text("У этой группы нет слотов.", reply_markup=kb_back("admin:slots"))

        await call.answer()

        return

    rows=[]

    for s in slots[:15]:

        dt=parse_dt(s["starts_at"])

        rows.append([__import__("aiogram").types.InlineKeyboardButton(

            text=f"{fmt_dt(dt)}",

            callback_data=f"admin:slot:open:{s['slot_id']}"

        )])

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:slots")])

    kb=__import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text(f"Слоты группы {gid}:", reply_markup=kb)

    await call.answer()



@router.callback_query(F.data.startswith("admin:slot:open:"))

async def cb_admin_slot_open(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    slot_id=int(call.data.split(":")[-1])

    slot=await db.get_slot(slot_id)

    if not slot:

        await call.answer("Слот не найден.", show_alert=True)

        return

    starts=parse_dt(slot["starts_at"])

    booked=await db.count_active_bookings("training", slot_id)

    text=(

        f"<b>Слот</b> #{slot_id}\n"

        f"Группа: {slot['group_id']}\n"

        f"🕒 {fmt_dt(starts)}\n"

        f"👥 {booked}/{slot['capacity']}\n\n"

        f"Кнопки ниже: список записанных (с оплатой)."

    )

    # reuse message keyboard: open users list

    rows=[
        [__import__("aiogram").types.InlineKeyboardButton(text="👥 Записанные", callback_data=f"admin:training:{slot_id}:users:page:0")],
        [__import__("aiogram").types.InlineKeyboardButton(text="➕ Записать человека", callback_data=f"admin:training:book:{slot_id}:admin")],
        [__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:slot:list:{slot['group_id']}")]
    ]

    kb=__import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text(text, reply_markup=kb)

    await call.answer()



@router.callback_query(F.data.startswith("admin:training:") & F.data.contains(":users:page:"))

async def cb_admin_training_users(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    parts=call.data.split(":")

    slot_id=int(parts[2])

    page=int(parts[-1])

    limit=15

    offset=page*limit

    total=await db.count_entity_bookings("training", slot_id)

    items=await db.list_entity_bookings("training", slot_id, offset, limit)

    lines=[f"<b>Записанные (слот #{slot_id})</b> ({total}):"]

    rows=[]

    for i, it in enumerate(items, start=offset+1):

        st="✅" if it.get("pay_status")=="confirmed" else "⏳"
        uname=f"@{it['username']}" if it.get("username") else ""
        seats = int(it.get("seats", 1))
        seat_suffix = f" x{seats}" if seats > 1 else ""
        lines.append(f"{i}) {it['full_name']} {uname}{seat_suffix} — {st}".strip())
        rows.append([__import__("aiogram").types.InlineKeyboardButton(
            text=f"{st} {it['full_name']}{seat_suffix}",
            callback_data=f"admin:pay:toggle:{it['booking_id']}:{slot_id}:{page}"
        )])

    nav=[]

    if page>0: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="⬅️", callback_data=f"admin:training:{slot_id}:users:page:{page-1}"))

    if offset+limit<total: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="➡️", callback_data=f"admin:training:{slot_id}:users:page:{page+1}"))

    if nav: rows.append(nav)
    rows.append([__import__("aiogram").types.InlineKeyboardButton(
        text="➕ Записать человека",
        callback_data=f"admin:training:book:{slot_id}:admin"
    )])

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:slot:open:{slot_id}")])

    kb=__import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text("\n".join(lines), reply_markup=kb)

    await call.answer()



@router.callback_query(F.data.startswith("admin:pay:toggle:"))

async def cb_admin_pay_toggle(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    # admin:pay:toggle:<booking_id>:<slot_id>:<page>

    _,_,_,booking_id, slot_id, page = call.data.split(":")

    new_status = await db.toggle_payment(int(booking_id), call.from_user.id)

    await call.answer("Оплата: " + ("✅ подтверждена" if new_status=="confirmed" else "⏳ ожидает"))

    # refresh list

    await cb_admin_training_users(CallbackQuery(

        id=call.id, from_user=call.from_user, chat_instance=call.chat_instance,

        message=call.message, data=f"admin:training:{slot_id}:users:page:{page}"

    ))



# ----------- admin: payment settings -----------

@router.callback_query(F.data.startswith("admin:pay:tournament:toggle:"))
async def cb_admin_pay_tournament_toggle(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    # admin:pay:tournament:toggle:<booking_id>:<tournament_id>:<page>
    parts = call.data.split(":")
    booking_id = int(parts[4])
    tournament_id = int(parts[5])
    page = int(parts[6])
    new_status = await db.toggle_payment(booking_id, call.from_user.id)
    await call.answer("\u041e\u043f\u043b\u0430\u0442\u0430: " + ("\u2705 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0430" if new_status == "confirmed" else "\u23f3 \u043e\u0436\u0438\u0434\u0430\u0435\u0442"))
    await cb_admin_tournament_users(CallbackQuery(
        id=call.id, from_user=call.from_user, chat_instance=call.chat_instance,
        message=call.message, data=f"admin:tournament:{tournament_id}:users:page:{page}"
    ))

@router.callback_query(F.data == "admin:payset")
async def cb_admin_payset(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    s = await db.get_payment_settings()
    amount = s.get("amount")
    amount_text = (
        f"\u0421\u0443\u043c\u043c\u0430: <b>{amount}</b>"
        if amount is not None
        else "\u0421\u0443\u043c\u043c\u0430: \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u0430"
    )
    text = (
        "<b>\u041e\u043f\u043b\u0430\u0442\u0430: \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438</b>\n\n"
        f"\u0422\u0435\u043a\u0443\u0449\u0438\u0439 \u0442\u0435\u043a\u0441\u0442:\n{s.get('text','')}\n\n"
        f"{amount_text}\n\n"
        "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435 \u043a\u043d\u043e\u043f\u043a\u0438 \u043d\u0438\u0436\u0435 \u0434\u043b\u044f \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0439."
    )
    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(text="\u270d\ufe0f \u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0442\u0435\u043a\u0441\u0442", callback_data="admin:payset:edit")],
        [__import__("aiogram").types.InlineKeyboardButton(text="\U0001F4B0 \u0423\u043a\u0430\u0437\u0430\u0442\u044c \u0441\u0443\u043c\u043c\u0443", callback_data="admin:payset:amount")],
        [__import__("aiogram").types.InlineKeyboardButton(text="\U0001F9F9 \u0421\u0431\u0440\u043e\u0441\u0438\u0442\u044c \u043e\u043f\u043b\u0430\u0442\u0443", callback_data="admin:payset:reset")],
        [__import__("aiogram").types.InlineKeyboardButton(text="\u2b05\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data="admin:root")],
    ]
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "admin:notifyset")
async def cb_admin_notifyset(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    s = await db.get_notify_settings()
    text = (
        "<b>Оповещения: настройки</b>\n\n"
        f"Текущий текст:\n{s.get('text','')}\n\n"
        "Используйте кнопку ниже для изменения."
    )
    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(text="✏️ Изменить текст", callback_data="admin:notifyset:edit")],
        [__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:root")],
    ]
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "admin:notifyset:edit")
async def cb_admin_notifyset_edit(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await db.set_mode(call.from_user.id, "admin_notifyset:text")
    await call.message.edit_text(
        "Введите текст уведомления.\n"
        "Можно использовать несколько строк.\n"
        "/cancel — отмена.",
        reply_markup=kb_back("admin:notifyset"),
    )
    await call.answer()


@router.callback_query(F.data == "admin:payset:edit")
async def cb_admin_payset_edit(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    await db.set_mode(call.from_user.id, "admin_payset:text")
    await call.message.edit_text(
        "\u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u043d\u043e\u0432\u044b\u043c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435\u043c \u0442\u0435\u043a\u0441\u0442 \u043e\u043f\u043b\u0430\u0442\u044b.\n"
        "/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0430",
        reply_markup=kb_back("admin:payset"),
    )
    await call.answer()


@router.callback_query(F.data == "admin:payset:amount")
async def cb_admin_payset_amount(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    await db.set_mode(call.from_user.id, "admin_payset:amount")
    await call.message.edit_text(
        "\u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0441\u0443\u043c\u043c\u0443 \u0447\u0438\u0441\u043b\u043e\u043c (\u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440 3500).\n"
        "/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0430",
        reply_markup=kb_back("admin:payset"),
    )
    await call.answer()


@router.callback_query(F.data == "admin:payset:reset")
async def cb_admin_payset_reset(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(text="\u2705 \u0414\u0430, \u0441\u0431\u0440\u043e\u0441\u0438\u0442\u044c", callback_data="admin:payset:reset:confirm")],
        [__import__("aiogram").types.InlineKeyboardButton(text="\u274c \u041e\u0442\u043c\u0435\u043d\u0430", callback_data="admin:payset")],
    ]
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text(
        "\u0412\u044b \u0443\u0432\u0435\u0440\u0435\u043d\u044b, \u0447\u0442\u043e \u0445\u043e\u0442\u0438\u0442\u0435 \u0441\u0431\u0440\u043e\u0441\u0438\u0442\u044c \u0442\u0435\u043a\u0441\u0442 \u0438 \u0441\u0443\u043c\u043c\u0443 \u043e\u043f\u043b\u0430\u0442\u044b?",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data == "admin:payset:reset:confirm")
async def cb_admin_payset_reset_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.", show_alert=True)
        return
    await db.set_payment_settings("\u041e\u043f\u043b\u0430\u0442\u0430: \u0443\u0442\u043e\u0447\u043d\u0438\u0442\u0435 \u0443 \u0442\u0440\u0435\u043d\u0435\u0440\u0430.", None)
    await call.message.edit_text(
        "\u0421\u0431\u0440\u043e\u0448\u0435\u043d\u043e.\n\u0422\u0435\u043a\u0441\u0442 \u0438 \u0441\u0443\u043c\u043c\u0430 \u043e\u043f\u043b\u0430\u0442\u044b \u043e\u0447\u0438\u0449\u0435\u043d\u044b.",
        reply_markup=kb_back("admin:payset"),
    )
    await call.answer()


@router.callback_query(F.data == "admin:bc")
async def cb_admin_bc(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(text="👥 Всем", callback_data="admin:bc:all")],
        [__import__("aiogram").types.InlineKeyboardButton(text="🎯 Выбрать группу", callback_data="admin:bc:pickgroup:page:0")],
        [__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:root")],
    ]
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text("Рассылка: выберите получателей.", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "admin:bc:all")
async def cb_admin_bc_all(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    draft = ADMIN_DRAFTS.setdefault(call.from_user.id, {"type": "bc"})
    draft["target_gid"] = None
    await db.set_mode(call.from_user.id, "admin_bc:compose")
    await call.message.edit_text(
        "Рассылка всем.\n"
        "Отправьте сообщение с текстом.\n"
        "\/cancel — отмена",
        reply_markup=kb_back("admin:bc"),
    )
    await call.answer()

@router.callback_query(F.data.startswith("admin:bc:pickgroup:page:"))
async def cb_admin_bc_pickgroup_page(call: CallbackQuery):
    page = int(call.data.split(":")[-1])
    await cb_admin_bc_pickgroup(call, page)

async def cb_admin_bc_pickgroup(call: CallbackQuery, page: int):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    limit = 8
    offset = page * limit
    total = await db.count_groups()
    if total == 0:
        await call.message.edit_text(
            "Групп пока нет. Сначала создайте группу.",
            reply_markup=kb_back("admin:root"),
        )
        await call.answer()
        return
    groups = await db.list_groups(offset, limit)
    rows = []
    for g in groups:
        rows.append([__import__("aiogram").types.InlineKeyboardButton(
            text=f"{g['group_id']}. {g['title']}",
            callback_data=f"admin:bc:group:{g['group_id']}"
        )])
    nav = []
    if page > 0:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="⬅️", callback_data=f"admin:bc:pickgroup:page:{page-1}"))
    if offset + limit < total:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="➡️", callback_data=f"admin:bc:pickgroup:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:bc")])
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text("Выберите группу для рассылки:", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("admin:bc:group:"))
async def cb_admin_bc_group(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    group_id = int(call.data.split(":")[-1])
    g = await db.get_group(group_id)
    if not g:
        await call.answer("Группа не найдена.", show_alert=True)
        return
    draft = ADMIN_DRAFTS.setdefault(call.from_user.id, {"type": "bc"})
    draft["target_gid"] = group_id
    await db.set_mode(call.from_user.id, "admin_bc:compose")
    await call.message.edit_text(
        f"Рассылка в группу <b>{g['title']}</b>.\n"
        "Отправьте сообщение с текстом.\n"
        "\/cancel — отмена",
        reply_markup=kb_back("admin:bc"),
    )
    await call.answer()

# ---------------- message handler for admin modes ----------------

@router.message()

async def message_router(message: Message):

    mode = await db.get_mode(message.from_user.id)

    if not mode:

        return

    if message.text and message.text.strip() == "/cancel":

        await db.set_mode(message.from_user.id, None)

        ADMIN_DRAFTS.pop(message.from_user.id, None)

        await message.answer("Отменено.", reply_markup=kb_main(is_admin(message.from_user.id)))

        return



    # create group

    if mode == "admin_create_group:title":

        title = (message.text or "").strip()

        if not title:

            await message.answer("Пусто. Введите название группы.")

            return

        gid = await db.create_group(title)

        await db.set_mode(message.from_user.id, None)

        await message.answer(f"Группа создана. ID: <b>{gid}</b>", reply_markup=kb_admin_root())

        return



    # set group schedule photo

    if mode.startswith("admin_group_sched:"):

        group_id = int(mode.split(":")[1])

        if not message.photo:

            await message.answer("Нужна картинка (фото). Отправьте фото.")

            return

        file_id = message.photo[-1].file_id

        await db.set_group_schedule(group_id, file_id)

        await db.set_mode(message.from_user.id, None)

        await message.answer("Расписание обновлено.", reply_markup=kb_admin_root())

        return



    # group title update
    if mode.startswith("admin_group_title:"):
        group_id = int(mode.split(":")[1])
        title = (message.text or "").strip()
        if not title:
            await message.answer("Пусто. Введите название группы.")
            return
        await db.update_group_title(group_id, title)
        await db.set_mode(message.from_user.id, None)
        g = await db.get_group(group_id)
        title_out = g["title"] if g else title
        await message.answer(
            f"Название обновлено: <b>{title_out}</b>\nID: {group_id}",
            reply_markup=kb_group_actions(group_id),
        )
        return


    if mode.startswith("admin_training_book:"):
        parts = mode.split(":")
        slot_id = int(parts[1])
        back_mode = parts[2] if len(parts) > 2 else "admin"
        name = (message.text or "").strip()
        if not name:
            await message.answer("Пусто. Введите имя.")
            return
        slot = await db.get_slot(slot_id)
        if not slot:
            await message.answer("Слот не найден.", reply_markup=kb_admin_root())
            await db.set_mode(message.from_user.id, None)
            return
        booked = await db.count_active_bookings("training", slot_id)
        if booked >= slot["capacity"]:
            back_to = f"admin:slot:open:{slot_id}" if back_mode == "admin" else f"train:open:{slot_id}"
            await message.answer("Мест нет.", reply_markup=kb_back(back_to))
            await db.set_mode(message.from_user.id, None)
            return
        guest_id = await db.create_guest_user(name, None)
        await db.create_booking(guest_id, "training", slot_id, status="active")
        await db.set_mode(message.from_user.id, None)
        back_to = f"admin:slot:open:{slot_id}" if back_mode == "admin" else f"train:open:{slot_id}"
        await message.answer("Записал ✅", reply_markup=kb_back(back_to))
        return


    # group settings update
    if mode.startswith("admin_group_settings:"):
        parts = mode.split(":")
        if len(parts) < 3:
            await message.answer("\u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u044b\u0439 \u0440\u0435\u0436\u0438\u043c.")
            await db.set_mode(message.from_user.id, None)
            return
        kind = parts[1]
        group_id = int(parts[2])

        if kind == "open_time":
            raw = (message.text or "").strip()
            if ":" not in raw:
                await message.answer("\u041d\u0443\u0436\u043d\u043e HH:MM. \u041f\u0440\u0438\u043c\u0435\u0440: 10:00")
                return
            try:
                hh, mm = raw.split(":", 1)
                hour = int(hh)
                minute = int(mm)
                if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                    raise ValueError
            except Exception:
                await message.answer("\u041d\u0443\u0436\u043d\u043e HH:MM. \u041f\u0440\u0438\u043c\u0435\u0440: 10:00")
                return
            await db.update_group_settings(group_id, open_time=raw)

        elif kind == "cancel_min":
            raw = (message.text or "").strip()
            if not raw.isdigit():
                await message.answer("\u041d\u0443\u0436\u043d\u043e \u0447\u0438\u0441\u043b\u043e. \u041f\u0440\u0438\u043c\u0435\u0440: 360")
                return
            await db.update_group_settings(group_id, cancel_minutes_before=int(raw))

        elif kind == "close_min":
            raw = (message.text or "").strip()
            if not raw.isdigit():
                await message.answer("\u041d\u0443\u0436\u043d\u043e \u0447\u0438\u0441\u043b\u043e. \u041f\u0440\u0438\u043c\u0435\u0440: 30")
                return
            await db.update_group_settings(group_id, close_minutes_before=int(raw))
        else:
            await message.answer("\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435.")
            return

        await db.set_mode(message.from_user.id, None)
        text, kb = await build_group_settings_view(group_id)
        await message.answer(text, reply_markup=kb)
        return

    # slot create multi-step

    if mode.startswith("admin_slot_create:"):

        step = mode.split(":")[1]

        draft = ADMIN_DRAFTS.setdefault(message.from_user.id, {"type":"slot"})

        if step == "time":

            raw = (message.text or "").strip()

            if ":" not in raw:

                await message.answer("Неверный формат времени. Пример: 19:00")

                return

            try:

                hh, mm = raw.split(":", 1)

                hour = int(hh)

                minute = int(mm)

                if hour < 0 or hour > 23 or minute < 0 or minute > 59:

                    raise ValueError

            except Exception:

                await message.answer("Неверный формат времени. Пример: 19:00")

                return

            weekday = draft.get("weekday")

            if weekday is None:

                await message.answer("Не выбран день недели. Начните заново.")

                return

            dt = next_weekday_datetime(int(weekday), raw)

            draft["starts_at"] = dt.isoformat()

            await db.set_mode(message.from_user.id, "admin_slot_create:capacity")

            await message.answer("Шаг 3/3: отправьте лимит мест (число). Можно с примечанием: 6;Тренировка в зале")

            return

        if step == "capacity":

            raw=(message.text or "").strip()

            note=None

            if ";" in raw:

                cap_s, note = raw.split(";",1)

                raw=cap_s.strip()

                note=note.strip() or None

            if not raw.isdigit():

                await message.answer("Нужно число. Пример: 6 или 6;Примечание")

                return

            cap=int(raw)

            slot_id = await db.create_slot(draft["group_id"], draft["starts_at"], cap, note)

            ADMIN_DRAFTS.pop(message.from_user.id, None)

            await db.set_mode(message.from_user.id, None)

            await message.answer(f"Слот создан: #{slot_id}", reply_markup=kb_admin_root())

            return



    

    # tournament create multi-step
    if mode.startswith("admin_tournament_create:"):
        step = mode.split(":")[1]
        draft = ADMIN_DRAFTS.setdefault(message.from_user.id, {"type": "tournament"})

        if step == "title":
            title = (message.text or "").strip()
            if not title:
                await message.answer("Пусто. Введите название турнира.")
                return
            draft["title"] = title
            await db.set_mode(message.from_user.id, "admin_tournament_create:starts_at")
            await message.answer("Шаг 2/5: отправьте дату/время в формате YYYY-MM-DD HH:MM (например 2026-01-30 19:00)")
            return

        if step == "starts_at":
            raw = (message.text or "").strip()
            try:
                from datetime import datetime
                dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
                dt = dt.replace(tzinfo=tz_now(TZ_OFFSET_HOURS).tzinfo)
            except Exception:
                await message.answer("Неверный формат. Пример: 2026-01-30 19:00")
                return
            draft["starts_at"] = dt.isoformat()
            await db.set_mode(message.from_user.id, "admin_tournament_create:capacity")
            await message.answer("Шаг 3/5: отправьте лимит мест (число).")
            return

        if step == "capacity":
            raw = (message.text or "").strip()
            if not raw.isdigit():
                await message.answer("Нужно число. Пример: 16")
                return
            draft["capacity"] = int(raw)
            await db.set_mode(message.from_user.id, "admin_tournament_create:waitlist")
            await message.answer("Шаг 4/5: лимит листа ожидания (число, 0 = без листа ожидания).")
            return

        if step == "waitlist":
            raw = (message.text or "").strip()
            if not raw.isdigit():
                await message.answer("Нужно число. Пример: 10 или 0")
                return
            draft["waitlist_limit"] = int(raw)
            await db.set_mode(message.from_user.id, "admin_tournament_create:description")
            await message.answer("Шаг 5/5: описание (или отправьте '-' чтобы пропустить).")
            return

        if step == "description":
            raw = (message.text or "").strip()
            desc = None if raw in ("-", "") else raw
            draft["description"] = desc

            group_id = draft.get("group_id")
            if not group_id:
                await message.answer("Не выбрана группа.")
                await db.set_mode(message.from_user.id, None)
                ADMIN_DRAFTS.pop(message.from_user.id, None)
                return

            s = await db.get_group_settings(group_id)
            close_mode = (s or {}).get("close_mode", "at_start")
            close_min = (s or {}).get("close_minutes_before")
            cancel_min = (s or {}).get("cancel_minutes_before", 360)

            tournament_id = await db.create_tournament(
                draft["title"],
                draft["starts_at"],
                draft["capacity"],
                None,
                draft.get("description"),
                close_mode=close_mode,
                close_minutes_before=close_min,
                cancel_minutes_before=cancel_min,
                waitlist_limit=draft.get("waitlist_limit", 0),
            )
            await db.add_tournament_group(tournament_id, group_id)

            ADMIN_DRAFTS.pop(message.from_user.id, None)
            await db.set_mode(message.from_user.id, None)

            await message.answer(
                f"Турнир создан: #{tournament_id}\n"
                "Запись открыта сразу. Закрытие — по настройкам группы.",
                reply_markup=kb_admin_root(),
            )
            return

    # tournament settings update
    if mode.startswith("admin_tournament_settings:"):
        parts = mode.split(":")
        if len(parts) < 3:
            await message.answer("\u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u044b\u0439 \u0440\u0435\u0436\u0438\u043c.")
            await db.set_mode(message.from_user.id, None)
            return
        kind = parts[1]
        tournament_id = int(parts[2])
        raw = (message.text or "").strip()

        if kind == "title":
            if not raw:
                await message.answer("\u041f\u0443\u0441\u0442\u043e.")
                return
            await db.update_tournament_settings(tournament_id, title=raw)

        elif kind == "starts_at":
            try:
                from datetime import datetime
                dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
                dt = dt.replace(tzinfo=tz_now(TZ_OFFSET_HOURS).tzinfo)
                await db.update_tournament_settings(tournament_id, starts_at=dt.isoformat())
            except Exception:
                await message.answer("\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u0444\u043e\u0440\u043c\u0430\u0442. \u041f\u0440\u0438\u043c\u0435\u0440: 2026-01-30 19:00")
                return

        elif kind == "capacity":
            if not raw.isdigit():
                await message.answer("\u041d\u0443\u0436\u043d\u043e \u0447\u0438\u0441\u043b\u043e. \u041f\u0440\u0438\u043c\u0435\u0440: 20")
                return
            await db.update_tournament_settings(tournament_id, capacity=int(raw))

        elif kind == "waitlist":
            if not raw.isdigit():
                await message.answer("\u041d\u0443\u0436\u043d\u043e \u0447\u0438\u0441\u043b\u043e. \u041f\u0440\u0438\u043c\u0435\u0440: 10")
                return
            await db.update_tournament_settings(tournament_id, waitlist_limit=int(raw))

        elif kind == "amount":
            if not raw.isdigit():
                await message.answer("\u041d\u0443\u0436\u043d\u043e \u0447\u0438\u0441\u043b\u043e. \u041f\u0440\u0438\u043c\u0435\u0440: 3500")
                return
            await db.update_tournament_settings(tournament_id, amount=int(raw))

        elif kind == "close_min":
            if not raw.isdigit():
                await message.answer("\u041d\u0443\u0436\u043d\u043e \u0447\u0438\u0441\u043b\u043e. \u041f\u0440\u0438\u043c\u0435\u0440: 30")
                return
            await db.update_tournament_settings(tournament_id, close_minutes_before=int(raw))

        elif kind == "cancel_min":
            if not raw.isdigit():
                await message.answer("\u041d\u0443\u0436\u043d\u043e \u0447\u0438\u0441\u043b\u043e. \u041f\u0440\u0438\u043c\u0435\u0440: 360")
                return
            await db.update_tournament_settings(tournament_id, cancel_minutes_before=int(raw))

        elif kind == "description":
            desc = None if raw in ("-", "") else raw
            await db.update_tournament_settings(tournament_id, description=desc)

        else:
            await message.answer("\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435.")
            return

        await db.set_mode(message.from_user.id, None)
        text_out, kb = await build_tournament_settings_view(tournament_id)
        await message.answer(text_out, reply_markup=kb)
        return

# payment settings
# payment settings

    if mode == "admin_payset:text":

        txt = (message.text or "").strip()

        if not txt:

            await message.answer("Пустой текст.")

            return

        amount=None

        lines=[]

        for line in txt.splitlines():

            if line.strip().startswith("amount="):

                try:

                    amount=int(line.split("=",1)[1].strip())

                except Exception:

                    await message.answer("amount должен быть числом.")

                    return

            else:

                lines.append(line)

        final="\n".join(lines).strip() or "Оплата: уточните у тренера."

        await db.set_payment_settings(final, amount)

        await db.set_mode(message.from_user.id, None)

        await message.answer("\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u043e\u043f\u043b\u0430\u0442\u044b \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b.", reply_markup=kb_back("admin:payset"))

        return



    if mode == "admin_payset:amount":
        raw = (message.text or "").strip()
        if not raw.isdigit():
            await message.answer("Сумма должна быть числом.")
            return
        amount = int(raw)
        s = await db.get_payment_settings()
        text_val = s.get("text", "")
        await db.set_payment_settings(text_val, amount)
        await db.set_mode(message.from_user.id, None)
        await message.answer("\u0421\u0443\u043c\u043c\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0430.", reply_markup=kb_back("admin:payset"))
        return


    if mode == "admin_notifyset:text":
        txt = (message.text or "").strip()
        if not txt:
            await message.answer("Пустой текст.")
            return
        await db.set_notify_settings(txt)
        await db.set_mode(message.from_user.id, None)
        await message.answer("Текст уведомления сохранён.", reply_markup=kb_back("admin:notifyset"))
        return


    # broadcast
    if mode == "admin_bc:compose":
        txt = (message.text or "").strip()
        if not txt:
            await message.answer("Пустой текст.")
            return

        draft = ADMIN_DRAFTS.get(message.from_user.id, {})
        target_gid = draft.get("target_gid")
        prefix = ""
        if target_gid is None:
            prefix = "\u2693 \u0420\u0430\u0441\u0441\u044b\u043b\u043a\u0430 (\u0432\u0441\u0435\u043c)\n"
        else:
            g = await db.get_group(target_gid)
            g_title = g["title"] if g else f"#{target_gid}"
            prefix = f"\u2693 \u0420\u0430\u0441\u0441\u044b\u043b\u043a\u0430 (\u0433\u0440\u0443\u043f\u043f\u0430: {g_title})\n"
        full_text = prefix + txt

        async def iter_users():
            async with db.connect() as conn:
                if target_gid is None:
                    rows = await conn.execute_fetchall("SELECT user_id FROM users")
                else:
                    rows = await conn.execute_fetchall("SELECT user_id FROM users WHERE group_id=?", (target_gid,))
                for r in rows:
                    yield int(r["user_id"])

        sent = 0
        async for uid in iter_users():
            try:
                await bot.send_message(uid, full_text)
                sent += 1
            except Exception:
                pass

        await db.set_mode(message.from_user.id, None)
        ADMIN_DRAFTS.pop(message.from_user.id, None)
        await message.answer(f"Рассылка отправлена: {sent}", reply_markup=kb_admin_root())
        return


# ---------------- main ----------------

async def main():

    backup_dir = os.path.join(os.path.dirname(DATABASE_PATH) or ".", "backup")
    restore_db_if_default(DATABASE_PATH, backup_dir)
    await db.init()
    for uid in ADMIN_IDS:
        await db.add_admin(uid)
    global ADMIN_CACHE
    ADMIN_CACHE = set(await db.list_admins()) | set(ADMIN_IDS)

    logger.info("DB initialized")

    notify_task = asyncio.create_task(notify_open_loop())
    backup_task = asyncio.create_task(backup_loop(DATABASE_PATH, backup_dir))
    try:
        await dp.start_polling(bot)
    finally:
        notify_task.cancel()
        backup_task.cancel()



if __name__ == "__main__":

    asyncio.run(main())










