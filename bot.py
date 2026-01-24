import asyncio

import logging

import os

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

    tz_now, parse_dt, fmt_dt, compute_open_datetime, compute_close_datetime, compute_cancel_deadline

)



load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

DATABASE_PATH = os.getenv("DATABASE_PATH", "trainer_bot.db").strip()

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



# ---------------- helpers ----------------

def is_admin(user_id: int) -> bool:

    return user_id in ADMIN_IDS



def mention(full_name: str, username: Optional[str]) -> str:

    if username:

        return f"{full_name} (@{username})"

    return full_name



async def show_main(target: Message | CallbackQuery, user_id: int, text: Optional[str] = None):

    u = await db.get_user(user_id)

    gid = u.get("group_id") if u else None

    prefix = "Выберите действие:"

    if gid is None:

        prefix = "Вы не привязаны к группе. Попросите у тренера ссылку-приглашение."

    msg_text = text or prefix

    kb = kb_main(is_admin(user_id))

    if isinstance(target, CallbackQuery):

        await target.message.edit_text(msg_text, reply_markup=kb)

        await target.answer()

    else:

        await target.answer(msg_text, reply_markup=kb)



# ---------------- start ----------------

@router.message(CommandStart())

async def start_handler(message: Message):

    user = message.from_user

    await db.upsert_user(user.id, user.username or "", user.full_name or "")

    # deep link: /start g_<token>

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

    text = s.get("text") or "Оплата: уточните у тренера."

    await call.message.edit_text(text, reply_markup=kb_back("main"))

    await call.answer()



# ---------------- schedule ----------------

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

    await call.message.delete()

    await bot.send_photo(call.from_user.id, photo=file_id, caption=f"Расписание: <b>{g['title']}</b>", reply_markup=kb_back("main"))

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

            text=f"{fmt_dt(dt)} (лимит {s['capacity']})",

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



    can_join = (now >= open_dt) and (now < close_dt) and (booked < slot["capacity"]) and (my_booking is None)

    can_leave = (my_booking is not None) and (now < cancel_deadline)



    text = (

        f"<b>Занятие</b>\n"

        f"🕒 {fmt_dt(starts)}\n"

        f"👥 Мест: {booked}/{slot['capacity']}\n"

    )

    if slot.get("note"):

        text += f"📝 {slot['note']}\n"

    if now < open_dt:

        text += f"\nЗапись откроется: <b>{fmt_dt(open_dt)}</b>"

    elif now >= close_dt:

        text += f"\nЗапись закрыта."

    if my_booking:

        if now < cancel_deadline:

            text += f"\n\nВы записаны. Отмена возможна до <b>{fmt_dt(cancel_deadline)}</b>."

        else:

            text += f"\n\nВы записаны. Отмена уже недоступна."

    await call.message.edit_text(text, reply_markup=kb_slot_actions(slot_id, can_join, can_leave))

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

    await db.cancel_booking(booking["booking_id"])

    await call.answer("Отменил ❌")

    await cb_train_open(call)



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

    waitlist_limit = int(t.get("waitlist_limit") or 0)
    has_waitlist_spots = waitlist_limit > 0 and waitlist_count < waitlist_limit

    can_join = (now < close_dt) and (my_booking is None) and (booked < t["capacity"] or has_waitlist_spots)
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
            text += "\n\nВы записаны."
        if now < cancel_deadline:
            text += f" Отмена возможна до <b>{fmt_dt(cancel_deadline)}</b>."
        else:
            text += " Отмена уже недоступна."

    await call.message.edit_text(text, reply_markup=kb_tour_actions(tournament_id, can_join, can_leave, is_waitlist))
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



# ----------- admin: groups list/create/manage -----------

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



@router.callback_query(F.data.startswith("admin:group:") & ~F.data.contains(":"))

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



@router.callback_query(F.data.startswith("admin:group:") & F.data.endswith(":sched"))

async def cb_admin_group_sched(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    group_id = int(call.data.split(":")[2])

    await db.set_mode(call.from_user.id, f"admin_group_sched:{group_id}")

    await call.message.edit_text("Пришлите картинку расписания (фото) для этой группы.\n/cancel — отмена.")

    await call.answer()



@router.callback_query(F.data.startswith("admin:group:") & F.data.endswith(":settings"))
async def cb_admin_group_settings(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    group_id = int(call.data.split(":")[2])
    s = await db.get_group_settings(group_id)
    close_text = (
        "в момент начала"
        if s["close_mode"] == "at_start"
        else f"за {s.get('close_minutes_before')} мин."
    )
    text = (
        f"<b>Настройки группы {group_id}</b>\n"
        f"Открытие записи: за <b>{s['open_days_before']}</b> дн. в <b>{s['open_time']}</b>\n"
        f"Отмена записи: за <b>{s['cancel_minutes_before']}</b> мин.\n"
        f"Закрытие записи: <b>{close_text}</b>\n\n"
        f"Чтобы изменить, отправьте сообщением:\n"
        f"<code>open_days=2</code>\n"
        f"<code>open_time=10:00</code>\n"
        f"<code>cancel_min=360</code>\n"
        f"<code>close_mode=at_start</code> или <code>close_mode=minutes_before</code>\n"
        f"<code>close_min=30</code> (если minutes_before)\n\n"
        f"/cancel — выйти"
    )
    await db.set_mode(call.from_user.id, f"admin_group_settings:{group_id}")
    await call.message.edit_text(text, reply_markup=kb_back(f"admin:group:{group_id}"))
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
            [__import__("aiogram").types.InlineKeyboardButton(text="? Создать группу", callback_data="admin:group:create")],
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
        await call.answer("Нет доступа.", show_alert=True)
        return
    parts = call.data.split(":")
    tournament_id = int(parts[2])
    page = int(parts[-1])
    limit = 15
    offset = page * limit
    total = await db.count_entity_bookings("tournament", tournament_id, status="active")
    items = await db.list_entity_bookings("tournament", tournament_id, offset, limit, status="active")
    lines = [f"<b>Записанные (турнир #{tournament_id})</b> ({total}):"]
    for i, it in enumerate(items, start=offset+1):
        uname = f"@{it['username']}" if it.get('username') else ""
        st = it.get("pay_status") or "pending"
        lines.append(f"{i}) {it['full_name']} {uname} — {st}".strip())
    kb = kb_admin_entity_users("tournament", tournament_id, page, page > 0, offset + limit < total, f"admin:tournament:open:{tournament_id}")
    await call.message.edit_text("\n".join(lines), reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("admin:tournament:") & F.data.endswith(":settings"))
async def cb_admin_tournament_settings(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    tournament_id = int(call.data.split(":")[2])
    t = await db.get_tournament(tournament_id)
    if not t:
        await call.answer("Турнир не найден.", show_alert=True)
        return
    close_text = (
        "Ð² Ð¼Ð¾Ð¼ÐµÐ½Ñ Ð½Ð°ÑÐ°Ð»Ð°" if t["close_mode"] == "at_start"
        else f"Ð·Ð° {t.get('close_minutes_before')} Ð¼Ð¸Ð½."
    )
    text = (
        f"<b>Настройки турнира {tournament_id}</b>\n"
        f"Название: {t['title']}\n"
        f"Дата: {t['starts_at']}\n"
        f"Мест: {t['capacity']}\n"
        f"Лист ожидания: {t.get('waitlist_limit', 0)}\n"
        f"Закрытие записи: {close_text}\n"
        f"Отмена: за {t['cancel_minutes_before']} мин.\n\n"
        "Чтобы изменить, отправьте сообщением (каждая строка key=value):\n"
        "title=...\n"
        "starts_at=YYYY-MM-DD HH:MM\n"
        "capacity=20\n"
        "waitlist=10\n"
        "close_mode=at_start|minutes_before\n"
        "close_min=30\n"
        "cancel_min=360\n"
        "description=...\n\n"
        "/cancel — выйти"
    )
    await db.set_mode(call.from_user.id, f"admin_tournament_settings:{tournament_id}")
    await call.message.edit_text(text, reply_markup=kb_back(f"admin:tournament:open:{tournament_id}"))
    await call.answer()


@router.callback_query(F.data == "admin:slot:create")

async def cb_admin_slot_create(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    await db.set_mode(call.from_user.id, "admin_slot_create:group_id")

    await call.message.edit_text(

        "Создание слота.\n"

        "Шаг 1/3: отправьте ID группы.\n"

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

        lines.append(f"{i}) {it['full_name']} {uname} — {st}".strip())

        rows.append([__import__("aiogram").types.InlineKeyboardButton(

            text=f"{st} {it['full_name']}",

            callback_data=f"admin:pay:toggle:{it['booking_id']}:{slot_id}:{page}"

        )])

    nav=[]

    if page>0: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="⬅️", callback_data=f"admin:training:{slot_id}:users:page:{page-1}"))

    if offset+limit<total: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="➡️", callback_data=f"admin:training:{slot_id}:users:page:{page+1}"))

    if nav: rows.append(nav)

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

@router.callback_query(F.data == "admin:payset")

async def cb_admin_payset(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    s = await db.get_payment_settings()

    text = (

        "<b>Оплата: настройки</b>\n\n"

        f"Текущий текст:\n{s.get('text','')}\n\n"

        "Отправьте новым сообщением текст оплаты.\n"

        "Если хотите указать сумму — добавьте отдельной строкой: <code>amount=3500</code>\n"

        "/cancel — отмена"

    )

    await db.set_mode(call.from_user.id, "admin_payset:text")

    await call.message.edit_text(text, reply_markup=kb_back("admin:root"))

    await call.answer()



# ----------- admin: broadcast -----------

@router.callback_query(F.data == "admin:bc")

async def cb_admin_bc(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("Нет доступа.", show_alert=True)

        return

    await db.set_mode(call.from_user.id, "admin_bc:compose")

    await call.message.edit_text(

        "Рассылка (текст).\n"

        "Отправьте сообщение с текстом.\n"

        "Можно начать строкой: <code>group_id=1</code> (если нужно только в одну группу).\n"

        "Без group_id — всем.\n"

        "/cancel — отмена"

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



    # group settings update

    if mode.startswith("admin_group_settings:"):

        group_id = int(mode.split(":")[1])

        text = (message.text or "").strip()

        if not text:

            await message.answer("Пусто.")

            return

        updates={}

        for line in text.splitlines():

            line=line.strip()

            if not line or "=" not in line:

                continue

            k,v=line.split("=",1)

            k=k.strip(); v=v.strip()

            if k=="open_days":

                updates["open_days_before"]=int(v)

            elif k=="open_time":

                updates["open_time"]=v

            elif k=="cancel_min":

                updates["cancel_minutes_before"]=int(v)

            elif k=="close_mode":

                if v not in ("at_start","minutes_before"):

                    await message.answer("close_mode должен быть at_start или minutes_before")

                    return

                updates["close_mode"]=v

            elif k=="close_min":

                updates["close_minutes_before"]=int(v)

        if not updates:

            await message.answer("Не нашёл параметров. Пример: open_days=2")

            return

        await db.update_group_settings(group_id, **updates)

        await db.set_mode(message.from_user.id, None)

        await message.answer("Настройки сохранены.", reply_markup=kb_admin_root())

        return



    # slot create multi-step

    if mode.startswith("admin_slot_create:"):

        step = mode.split(":")[1]

        draft = ADMIN_DRAFTS.setdefault(message.from_user.id, {"type":"slot"})

        if step == "group_id":

            raw=(message.text or "").strip()

            if not raw.isdigit():

                await message.answer("Нужно число — ID группы.")

                return

            gid=int(raw)

            g=await db.get_group(gid)

            if not g:

                await message.answer("Группа не найдена.")

                return

            draft["group_id"]=gid

            await db.set_mode(message.from_user.id, "admin_slot_create:starts_at")

            await message.answer("Шаг 2/3: отправьте дату/время в формате YYYY-MM-DD HH:MM (например 2026-01-30 19:00)")

            return

        if step == "starts_at":

            raw=(message.text or "").strip()

            try:

                # interpret as local tz, store as iso with offset

                from datetime import datetime, timezone

                dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")

                dt = dt.replace(tzinfo=tz_now(TZ_OFFSET_HOURS).tzinfo)

            except Exception:

                await message.answer("Неверный формат. Пример: 2026-01-30 19:00")

                return

            draft["starts_at"]=dt.isoformat()

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
        tournament_id = int(mode.split(":")[1])
        text_in = (message.text or "").strip()
        if not text_in:
            await message.answer("Пусто.")
            return

        updates = {}
        for line in text_in.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()

            if k == "title":
                updates["title"] = v
            elif k == "starts_at":
                try:
                    from datetime import datetime
                    dt = datetime.strptime(v, "%Y-%m-%d %H:%M")
                    dt = dt.replace(tzinfo=tz_now(TZ_OFFSET_HOURS).tzinfo)
                    updates["starts_at"] = dt.isoformat()
                except Exception:
                    await message.answer("Неверный формат. Пример: 2026-01-30 19:00")
                    return
            elif k == "capacity":
                updates["capacity"] = int(v)
            elif k == "waitlist":
                updates["waitlist_limit"] = int(v)
            elif k == "close_mode":
                if v not in ("at_start", "minutes_before"):
                    await message.answer("close_mode должен быть at_start или minutes_before")
                    return
                updates["close_mode"] = v
            elif k == "close_min":
                updates["close_minutes_before"] = int(v)
            elif k == "cancel_min":
                updates["cancel_minutes_before"] = int(v)
            elif k == "description":
                updates["description"] = None if v in ("-", "") else v

        if not updates:
            await message.answer("Не нашёл параметров. Пример: capacity=16")
            return

        await db.update_tournament_settings(tournament_id, **updates)
        await db.set_mode(message.from_user.id, None)
        await message.answer("Настройки сохранены.", reply_markup=kb_admin_root())
        return
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

        await message.answer("Настройки оплаты сохранены.", reply_markup=kb_admin_root())

        return



    # broadcast

    if mode == "admin_bc:compose":

        txt=(message.text or "").strip()

        if not txt:

            await message.answer("Пустой текст.")

            return

        target_gid=None

        lines=[]

        for line in txt.splitlines():

            if line.strip().startswith("group_id="):

                try:

                    target_gid=int(line.split("=",1)[1].strip())

                except Exception:

                    await message.answer("group_id должен быть числом.")

                    return

            else:

                lines.append(line)

        final="\n".join(lines).strip()

        async def iter_users():

            async with await db.connect() as conn:

                if target_gid is None:

                    rows = await conn.execute_fetchall("SELECT user_id FROM users")

                else:

                    rows = await conn.execute_fetchall("SELECT user_id FROM users WHERE group_id=?", (target_gid,))

                for r in rows:

                    yield int(r["user_id"])

        sent=0

        async for uid in iter_users():

            try:

                await bot.send_message(uid, final)

                sent+=1

            except Exception:

                pass

        await db.set_mode(message.from_user.id, None)

        await message.answer(f"Рассылка отправлена: {sent}", reply_markup=kb_admin_root())

        return



# ---------------- main ----------------

async def main():

    await db.init()

    logger.info("DB initialized")

    await dp.start_polling(bot)



if __name__ == "__main__":

    asyncio.run(main())









