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



async def show_main(target: Message | CallbackQuery, user_id: int, text: Optional[str] = None):

    u = await db.get_user(user_id)

    gid = u.get("group_id") if u else None

    prefix = "Р’С‹Р±РµСЂРёС‚Рµ РґРµР№СЃС‚РІРёРµ:"

    if gid is None:

        prefix = "Р’С‹ РЅРµ РїСЂРёРІСЏР·Р°РЅС‹ Рє РіСЂСѓРїРїРµ. РџРѕРїСЂРѕСЃРёС‚Рµ Сѓ С‚СЂРµРЅРµСЂР° СЃСЃС‹Р»РєСѓ-РїСЂРёРіР»Р°С€РµРЅРёРµ."

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

    # deep link: /start g_<token> or /start a_<token>

    payload = (message.text or "").split(maxsplit=1)

    if len(payload) == 2 and payload[1].startswith("g_"):

        token = payload[1][2:]

        gid = await db.resolve_invite(token)

        if gid:

            await db.set_user_group(user.id, gid)

            g = await db.get_group(gid)

            await message.answer(f"Р“РѕС‚РѕРІРѕ. Р’С‹ РґРѕР±Р°РІР»РµРЅС‹ РІ РіСЂСѓРїРїСѓ: <b>{g['title']}</b>")

        else:

            await message.answer("РЎСЃС‹Р»РєР° РЅРµРґРµР№СЃС‚РІРёС‚РµР»СЊРЅР° РёР»Рё РѕС‚РєР»СЋС‡РµРЅР°.")

    elif len(payload) == 2 and payload[1].startswith("a_"):
        token = payload[1][2:]
        if is_admin(user.id):
            await message.answer("Р вЂ™РЎвЂ№ РЎС“Р В¶Р Вµ Р В°Р Т‘Р СР С‘Р Р….")
        else:
            ok = await db.resolve_admin_invite(token)
            if ok:
                await db.add_admin(user.id)
                ADMIN_CACHE.add(user.id)
                await message.answer("Р вЂњР С•РЎвЂљР С•Р Р†Р С•. Р вЂ™РЎвЂ№ Р Т‘Р С•Р В±Р В°Р Р†Р В»Р ВµР Р…РЎвЂ№ Р Р† Р В°Р Т‘Р СР С‘Р Р…РЎвЂ№.")
            else:
                await message.answer("Р РЋРЎРѓРЎвЂ№Р В»Р С”Р В° Р Р…Р ВµР Т‘Р ВµР в„–РЎРѓРЎвЂљР Р†Р С‘РЎвЂљР ВµР В»РЎРЉР Р…Р В° Р С‘Р В»Р С‘ Р С•РЎвЂљР С”Р В»РЎР‹РЎвЂЎР ВµР Р…Р В°.")

    await show_main(message, user.id)



@router.message(Command("cancel"))

async def cancel_any(message: Message):

    await db.set_mode(message.from_user.id, None)

    ADMIN_DRAFTS.pop(message.from_user.id, None)

    await message.answer("РћС‚РјРµРЅРµРЅРѕ.", reply_markup=kb_main(is_admin(message.from_user.id)))



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
@router.callback_query(F.data == "sched:show")

async def cb_schedule(call: CallbackQuery):

    u = await db.get_user(call.from_user.id)

    gid = u.get("group_id") if u else None

    if not gid:

        await call.answer("РЎРЅР°С‡Р°Р»Р° РЅСѓР¶РЅРѕ Р±С‹С‚СЊ РІ РіСЂСѓРїРїРµ.", show_alert=True)

        return

    g = await db.get_group(gid)

    file_id = g.get("schedule_file_id")

    if not file_id:

        await call.message.edit_text("Р Р°СЃРїРёСЃР°РЅРёРµ РµС‰С‘ РЅРµ Р·Р°РіСЂСѓР¶РµРЅРѕ.", reply_markup=kb_back("main"))

        await call.answer()

        return

    await call.message.edit_text("\u0420\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043e\u0442\u043a\u0440\u044b\u0442\u043e \u043e\u0442\u0434\u0435\u043b\u044c\u043d\u044b\u043c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435\u043c.", reply_markup=kb_back("main"))

    await bot.send_photo(call.from_user.id, photo=file_id, caption=f"Р Р°СЃРїРёСЃР°РЅРёРµ: <b>{g['title']}</b>", reply_markup=kb_back("main"))

    await call.answer()



# ---------------- trainings: list/open/join/leave ----------------

@router.callback_query(F.data == "train:list")

async def cb_train_list(call: CallbackQuery):

    u = await db.get_user(call.from_user.id)

    gid = u.get("group_id") if u else None

    if not gid:

        await call.answer("РЎРЅР°С‡Р°Р»Р° РЅСѓР¶РЅРѕ Р±С‹С‚СЊ РІ РіСЂСѓРїРїРµ.", show_alert=True)

        return

    now = tz_now(TZ_OFFSET_HOURS)

    from_iso = (now - timedelta(days=1)).isoformat()

    to_iso = (now + timedelta(days=21)).isoformat()

    slots = await db.list_slots_for_group(gid, from_iso, to_iso, limit=30)

    if not slots:

        await call.message.edit_text("РџРѕРєР° РЅРµС‚ РґРѕСЃС‚СѓРїРЅС‹С… Р·Р°РЅСЏС‚РёР№.", reply_markup=kb_back("main"))

        await call.answer()

        return

    lines = ["<b>Р‘Р»РёР¶Р°Р№С€РёРµ Р·Р°РЅСЏС‚РёСЏ</b>:"]

    # show as buttons list (first 10) by edit message with inline keyboard per slot

    rows=[]

    for s in slots[:12]:

        dt = parse_dt(s["starts_at"])

        rows.append([__import__("aiogram").types.InlineKeyboardButton(

            text=f"{fmt_dt(dt)} (Р»РёРјРёС‚ {s['capacity']})",

            callback_data=f"train:open:{s['slot_id']}"

        )])

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="main")])

    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text("\n".join(lines), reply_markup=kb)

    await call.answer()



@router.callback_query(F.data.startswith("train:open:"))

async def cb_train_open(call: CallbackQuery):

    slot_id = int(call.data.split(":")[-1])

    slot = await db.get_slot(slot_id)

    if not slot or not slot.get("is_active"):

        await call.answer("РЎР»РѕС‚ РЅРµ РЅР°Р№РґРµРЅ.", show_alert=True)

        return

    u = await db.get_user(call.from_user.id)

    if not u or u.get("group_id") != slot["group_id"]:

        await call.answer("Р­С‚Рѕ Р·Р°РЅСЏС‚РёРµ РЅРµ РІР°С€РµР№ РіСЂСѓРїРїС‹.", show_alert=True)

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

        f"<b>Р—Р°РЅСЏС‚РёРµ</b>\n"

        f"рџ•’ {fmt_dt(starts)}\n"

        f"рџ‘Ґ РњРµСЃС‚: {booked}/{slot['capacity']}\n"

    )

    if slot.get("note"):

        text += f"рџ“ќ {slot['note']}\n"

    if now < open_dt:

        text += f"\nР—Р°РїРёСЃСЊ РѕС‚РєСЂРѕРµС‚СЃСЏ: <b>{fmt_dt(open_dt)}</b>"

    elif now >= close_dt:

        text += f"\nР—Р°РїРёСЃСЊ Р·Р°РєСЂС‹С‚Р°."

    if my_booking:

        if now < cancel_deadline:

            text += f"\n\nР’С‹ Р·Р°РїРёСЃР°РЅС‹. РћС‚РјРµРЅР° РІРѕР·РјРѕР¶РЅР° РґРѕ <b>{fmt_dt(cancel_deadline)}</b>."

        else:

            text += f"\n\nР’С‹ Р·Р°РїРёСЃР°РЅС‹. РћС‚РјРµРЅР° СѓР¶Рµ РЅРµРґРѕСЃС‚СѓРїРЅР°."

    await call.message.edit_text(text, reply_markup=kb_slot_actions(slot_id, can_join, can_leave))

    await call.answer()



@router.callback_query(F.data.startswith("train:join:"))

async def cb_train_join(call: CallbackQuery):

    slot_id = int(call.data.split(":")[-1])

    slot = await db.get_slot(slot_id)

    if not slot:

        await call.answer("РЎР»РѕС‚ РЅРµ РЅР°Р№РґРµРЅ.", show_alert=True)

        return

    u = await db.get_user(call.from_user.id)

    if not u or u.get("group_id") != slot["group_id"]:

        await call.answer("Р­С‚Рѕ Р·Р°РЅСЏС‚РёРµ РЅРµ РІР°С€РµР№ РіСЂСѓРїРїС‹.", show_alert=True)

        return

    settings = await db.get_group_settings(slot["group_id"])

    starts = parse_dt(slot["starts_at"])

    open_dt = compute_open_datetime(starts, settings["open_days_before"], settings["open_time"])

    close_dt = compute_close_datetime(starts, settings["close_mode"], settings.get("close_minutes_before"))

    now = tz_now(TZ_OFFSET_HOURS)

    if now < open_dt:

        await call.answer(f"Р—Р°РїРёСЃСЊ РѕС‚РєСЂРѕРµС‚СЃСЏ {fmt_dt(open_dt)}", show_alert=True)

        return

    if now >= close_dt:

        await call.answer("Р—Р°РїРёСЃСЊ Р·Р°РєСЂС‹С‚Р°.", show_alert=True)

        return

    booked = await db.count_active_bookings("training", slot_id)

    if booked >= slot["capacity"]:

        await call.answer("РњРµСЃС‚ РЅРµС‚.", show_alert=True)

        return

    existing = await db.get_user_booking(call.from_user.id, "training", slot_id)

    if existing:

        await call.answer("Р’С‹ СѓР¶Рµ Р·Р°РїРёСЃР°РЅС‹.", show_alert=True)

        return

    await db.create_booking(call.from_user.id, "training", slot_id)

    await call.answer("Р—Р°РїРёСЃР°Р» вњ…")

    await cb_train_open(call)



@router.callback_query(F.data.startswith("train:leave:"))

async def cb_train_leave(call: CallbackQuery):

    slot_id = int(call.data.split(":")[-1])

    slot = await db.get_slot(slot_id)

    if not slot:

        await call.answer("РЎР»РѕС‚ РЅРµ РЅР°Р№РґРµРЅ.", show_alert=True)

        return

    settings = await db.get_group_settings(slot["group_id"])

    starts = parse_dt(slot["starts_at"])

    cancel_deadline = compute_cancel_deadline(starts, settings["cancel_minutes_before"])

    now = tz_now(TZ_OFFSET_HOURS)

    booking = await db.get_user_booking(call.from_user.id, "training", slot_id)

    if not booking:

        await call.answer("Р’С‹ РЅРµ Р·Р°РїРёСЃР°РЅС‹.", show_alert=True)

        return

    if now >= cancel_deadline:

        await call.answer("РћС‚РјРµРЅР° СѓР¶Рµ РЅРµРґРѕСЃС‚СѓРїРЅР°.", show_alert=True)

        return

    await db.cancel_booking(booking["booking_id"])

    await call.answer("РћС‚РјРµРЅРёР» вќЊ")

    await cb_train_open(call)



# ---------------- tournaments ----------------
@router.callback_query(F.data == "tour:list")
async def cb_tour_list(call: CallbackQuery):
    u = await db.get_user(call.from_user.id)
    gid = u.get("group_id") if u else None
    if not gid:
        await call.answer("РЎРЅР°С‡Р°Р»Р° РЅСѓР¶РЅРѕ Р±С‹С‚СЊ РІ РіСЂСѓРїРїРµ.", show_alert=True)
        return
    now = tz_now(TZ_OFFSET_HOURS)
    from_iso = (now - timedelta(days=1)).isoformat()
    to_iso = (now + timedelta(days=30)).isoformat()
    tournaments = await db.list_tournaments_for_groups([gid], from_iso, to_iso, limit=30)
    if not tournaments:
        await call.message.edit_text("РџРѕРєР° РЅРµС‚ РґРѕСЃС‚СѓРїРЅС‹С… С‚СѓСЂРЅРёСЂРѕРІ.", reply_markup=kb_back("main"))
        await call.answer()
        return
    rows = []
    for t in tournaments[:12]:
        dt = parse_dt(t["starts_at"])
        rows.append([__import__("aiogram").types.InlineKeyboardButton(
            text=f"{fmt_dt(dt)} вЂ” {t['title']}",
            callback_data=f"tour:open:{t['tournament_id']}"
        )])
    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="main")])
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text("<b>РўСѓСЂРЅРёСЂС‹</b>:", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("tour:open:"))
async def cb_tour_open(call: CallbackQuery):
    tournament_id = int(call.data.split(":")[-1])
    t = await db.get_tournament(tournament_id)
    if not t or not t.get("is_active"):
        await call.answer("РўСѓСЂРЅРёСЂ РЅРµ РЅР°Р№РґРµРЅ.", show_alert=True)
        return
    u = await db.get_user(call.from_user.id)
    gid = u.get("group_id") if u else None
    if not gid:
        await call.answer("РЎРЅР°С‡Р°Р»Р° РЅСѓР¶РЅРѕ Р±С‹С‚СЊ РІ РіСЂСѓРїРїРµ.", show_alert=True)
        return
    groups = await db.list_tournament_groups(tournament_id)
    if gid not in groups:
        await call.answer("Р­С‚РѕС‚ С‚СѓСЂРЅРёСЂ РЅРµ РґР»СЏ РІР°С€РµР№ РіСЂСѓРїРїС‹.", show_alert=True)
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
        f"рџ•’ {fmt_dt(starts)}\n"
        f"рџ‘Ґ РњРµСЃС‚: {booked}/{t['capacity']}\n"
    )
    if waitlist_limit > 0:
        text += f"рџ“‹ Р›РёСЃС‚ РѕР¶РёРґР°РЅРёСЏ: {waitlist_count}/{waitlist_limit}\n"
    if t.get("description"):
        text += f"рџ“ќ {t['description']}\n"
    if now >= close_dt:
        text += "\nР—Р°РїРёСЃСЊ Р·Р°РєСЂС‹С‚Р°."
    if my_booking:
        if is_waitlist:
            text += "\n\nР’С‹ РІ Р»РёСЃС‚Рµ РѕР¶РёРґР°РЅРёСЏ."
        else:
            text += "\n\nР’С‹ Р·Р°РїРёСЃР°РЅС‹."
        if now < cancel_deadline:
            text += f" РћС‚РјРµРЅР° РІРѕР·РјРѕР¶РЅР° РґРѕ <b>{fmt_dt(cancel_deadline)}</b>."
        else:
            text += " РћС‚РјРµРЅР° СѓР¶Рµ РЅРµРґРѕСЃС‚СѓРїРЅР°."

    await call.message.edit_text(text, reply_markup=kb_tour_actions(tournament_id, can_join, can_leave, is_waitlist))
    await call.answer()

@router.callback_query(F.data.startswith("tour:join:"))
async def cb_tour_join(call: CallbackQuery):
    tournament_id = int(call.data.split(":")[-1])
    t = await db.get_tournament(tournament_id)
    if not t:
        await call.answer("РўСѓСЂРЅРёСЂ РЅРµ РЅР°Р№РґРµРЅ.", show_alert=True)
        return
    u = await db.get_user(call.from_user.id)
    gid = u.get("group_id") if u else None
    if not gid:
        await call.answer("РЎРЅР°С‡Р°Р»Р° РЅСѓР¶РЅРѕ Р±С‹С‚СЊ РІ РіСЂСѓРїРїРµ.", show_alert=True)
        return
    groups = await db.list_tournament_groups(tournament_id)
    if gid not in groups:
        await call.answer("Р­С‚РѕС‚ С‚СѓСЂРЅРёСЂ РЅРµ РґР»СЏ РІР°С€РµР№ РіСЂСѓРїРїС‹.", show_alert=True)
        return
    starts = parse_dt(t["starts_at"])
    close_dt = compute_close_datetime(starts, t["close_mode"], t.get("close_minutes_before"))
    now = tz_now(TZ_OFFSET_HOURS)
    if now >= close_dt:
        await call.answer("Р—Р°РїРёСЃСЊ Р·Р°РєСЂС‹С‚Р°.", show_alert=True)
        return
    existing = await db.get_user_booking_any(call.from_user.id, "tournament", tournament_id)
    if existing:
        await call.answer("Р’С‹ СѓР¶Рµ Р·Р°РїРёСЃР°РЅС‹.", show_alert=True)
        return
    booked = await db.count_active_bookings("tournament", tournament_id)
    waitlist_count = await db.count_bookings("tournament", tournament_id, "waitlist")
    waitlist_limit = int(t.get("waitlist_limit") or 0)
    if booked < t["capacity"]:
        await db.create_booking(call.from_user.id, "tournament", tournament_id, status="active")
        await call.answer("Р—Р°РїРёСЃР°Р» ?")
    elif waitlist_limit > 0 and waitlist_count < waitlist_limit:
        await db.create_booking(call.from_user.id, "tournament", tournament_id, status="waitlist")
        await call.answer("Р”РѕР±Р°РІРёР» РІ Р»РёСЃС‚ РѕР¶РёРґР°РЅРёСЏ ?")
    else:
        await call.answer("РњРµСЃС‚ РЅРµС‚.", show_alert=True)
        return
    await cb_tour_open(call)

@router.callback_query(F.data.startswith("tour:leave:"))
async def cb_tour_leave(call: CallbackQuery):
    tournament_id = int(call.data.split(":")[-1])
    t = await db.get_tournament(tournament_id)
    if not t:
        await call.answer("РўСѓСЂРЅРёСЂ РЅРµ РЅР°Р№РґРµРЅ.", show_alert=True)
        return
    starts = parse_dt(t["starts_at"])
    cancel_deadline = compute_cancel_deadline(starts, t["cancel_minutes_before"])
    now = tz_now(TZ_OFFSET_HOURS)
    booking = await db.get_user_booking_any(call.from_user.id, "tournament", tournament_id)
    if not booking:
        await call.answer("Р’С‹ РЅРµ Р·Р°РїРёСЃР°РЅС‹.", show_alert=True)
        return
    if now >= cancel_deadline:
        await call.answer("РћС‚РјРµРЅР° СѓР¶Рµ РЅРµРґРѕСЃС‚СѓРїРЅР°.", show_alert=True)
        return
    await db.cancel_booking(booking["booking_id"])

    if booking.get("status") == "active":
        next_wait = await db.pop_waitlist("tournament", tournament_id)
        if next_wait:
            await db.update_booking_status(next_wait["booking_id"], "active")
            try:
                await bot.send_message(
                    next_wait["user_id"],
                    f"Р’С‹ РїРµСЂРµРІРµРґРµРЅС‹ РёР· Р»РёСЃС‚Р° РѕР¶РёРґР°РЅРёСЏ РІ Р·Р°РїРёСЃСЊ РЅР° С‚СѓСЂРЅРёСЂ: <b>{t['title']}</b>.\n"
                    f"Р”Р°С‚Р°: {fmt_dt(starts)}"
                )
            except Exception:
                pass

    await call.answer("РћС‚РјРµРЅРёР» ?")
    await cb_tour_open(call)
# ---------------- admin root ----------------

@router.callback_query(F.data == "admin:root")
async def cb_admin_root(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    await call.message.edit_text("РђРґРјРёРЅ РјРµРЅСЋ:", reply_markup=kb_admin_root())
    await call.answer()

@router.callback_query(F.data == "admin:reset")
async def cb_admin_reset(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(text="вњ… Р”Р°, СЃР±СЂРѕСЃРёС‚СЊ РІСЃС‘", callback_data="admin:reset:confirm")],
        [__import__("aiogram").types.InlineKeyboardButton(text="вќЊ РћС‚РјРµРЅР°", callback_data="admin:root")],
    ]
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text(
        "Р’С‹ СѓРІРµСЂРµРЅС‹? Р­С‚Рѕ СѓРґР°Р»РёС‚ РіСЂСѓРїРїС‹, С‚СѓСЂРЅРёСЂС‹, СЃР»РѕС‚С‹, Р·Р°РїРёСЃРё, РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№, РёРЅРІР°Р№С‚С‹ Рё РїР»Р°С‚РµР¶Рё.",
        reply_markup=kb,
    )
    await call.answer()

@router.callback_query(F.data == "admin:reset:confirm")
async def cb_admin_reset_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    await db.reset_all()
    await call.message.edit_text("РЎР±СЂРѕСЃ РІС‹РїРѕР»РЅРµРЅ.", reply_markup=kb_admin_root())
    await call.answer()

@router.callback_query(F.data == "admin:invite_admin")
async def cb_admin_invite_admin(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    token = secrets.token_urlsafe(8)
    await db.create_admin_invite(token)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=a_{token}"
    await call.message.edit_text(
        "РЎСЃС‹Р»РєР° РґР»СЏ РґРѕР±Р°РІР»РµРЅРёСЏ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°:\n"
        f"{link}\n\n"
        "РџРµСЂРµРґР°Р№С‚Рµ СЌС‚Сѓ СЃСЃС‹Р»РєСѓ С‡РµР»РѕРІРµРєСѓ.",
        reply_markup=kb_back("admin:root"),
    )
    await call.answer()

@router.callback_query(F.data.startswith("admin:groups:page:"))

async def cb_admin_groups(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

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

        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ", callback_data=f"admin:groups:page:{page-1}"))

    if offset + limit < total:

        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="вћЎпёЏ", callback_data=f"admin:groups:page:{page+1}"))

    if nav:

        rows.append(nav)

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="вћ• РЎРѕР·РґР°С‚СЊ РіСЂСѓРїРїСѓ", callback_data="admin:group:create")])

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin:root")])

    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text("<b>Р“СЂСѓРїРїС‹</b>:", reply_markup=kb)

    await call.answer()



@router.callback_query(F.data == "admin:group:create")

async def cb_admin_group_create(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

        return

    await db.set_mode(call.from_user.id, "admin_create_group:title")

    await call.message.edit_text("Р’РІРµРґРёС‚Рµ РЅР°Р·РІР°РЅРёРµ РіСЂСѓРїРїС‹ (СЃРѕРѕР±С‰РµРЅРёРµРј).\n/cancel вЂ” РѕС‚РјРµРЅР°.", reply_markup=kb_back("admin:groups:page:0"))

    await call.answer()



@router.callback_query(F.data.regexp("^admin:group:\\d+$"))

async def cb_admin_group_open(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

        return

    group_id = int(call.data.split(":")[-1])

    g = await db.get_group(group_id)

    if not g:

        await call.answer("Р“СЂСѓРїРїР° РЅРµ РЅР°Р№РґРµРЅР°.", show_alert=True)

        return

    await call.message.edit_text(

        f"<b>Р“СЂСѓРїРїР°</b>: {g['title']}\nID: {group_id}",

        reply_markup=kb_group_actions(group_id)

    )

    await call.answer()



@router.callback_query(F.data.startswith("admin:group:") & F.data.endswith(":sched"))

async def cb_admin_group_sched(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

        return

    group_id = int(call.data.split(":")[2])

    await db.set_mode(call.from_user.id, f"admin_group_sched:{group_id}")

    await call.message.edit_text("РџСЂРёС€Р»РёС‚Рµ РєР°СЂС‚РёРЅРєСѓ СЂР°СЃРїРёСЃР°РЅРёСЏ (С„РѕС‚Рѕ) РґР»СЏ СЌС‚РѕР№ РіСЂСѓРїРїС‹.\n/cancel вЂ” РѕС‚РјРµРЅР°.")

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
        await call.answer("РќСЋРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
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

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

        return

    parts = call.data.split(":")

    group_id = int(parts[2])

    page = int(parts[-1])

    limit = 15

    offset = page * limit

    total = await db.count_group_users(group_id)

    users = await db.list_group_users(group_id, offset, limit)

    lines=[f"<b>РЈС‡РµРЅРёРєРё РіСЂСѓРїРїС‹ {group_id}</b> ({total}):"]

    for i, u in enumerate(users, start=offset+1):

        uname = f"@{u['username']}" if u.get("username") else ""

        lines.append(f"{i}) {u['full_name']} {uname}".strip())

    rows=[]

    nav=[]

    if page>0: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ", callback_data=f"admin:group:{group_id}:users:page:{page-1}"))

    if offset+limit<total: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="вћЎпёЏ", callback_data=f"admin:group:{group_id}:users:page:{page+1}"))

    if nav: rows.append(nav)

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data=f"admin:group:{group_id}")])

    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text("\n".join(lines), reply_markup=kb)

    await call.answer()



# ----------- admin: invites -----------
@router.callback_query(F.data == "admin:invites")
async def cb_admin_invites(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    total = await db.count_groups()
    if total == 0:
        rows = [
            [__import__("aiogram").types.InlineKeyboardButton(text="РЎРѕР·РґР°С‚СЊ РіСЂСѓРїРїСѓ", callback_data="admin:group:create")],
            [__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin:root")],
        ]
        kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
        await call.message.edit_text("Р“СЂСѓРїРї РµС‰С‘ РЅРµС‚. РЎРѕР·РґР°Р№С‚Рµ РіСЂСѓРїРїСѓ.", reply_markup=kb)
        await call.answer()
        return
    await cb_admin_invite_pickgroup(call, page=0)

@router.callback_query(F.data.startswith("admin:invite:pickgroup:page:"))
async def cb_admin_invite_pickgroup_cb(call: CallbackQuery):
    page = int(call.data.split(":")[-1])
    await cb_admin_invite_pickgroup(call, page=page)

async def cb_admin_invite_pickgroup(call: CallbackQuery, page: int):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
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
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ", callback_data=f"admin:invite:pickgroup:page:{page-1}"))
    if offset + limit < total:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="вћЎпёЏ", callback_data=f"admin:invite:pickgroup:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin:root")])
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text("РЎРѕР·РґР°РЅРёРµ РїСЂРёРіР»Р°СЃРёС‚РµР»СЊРЅРѕР№ СЃСЃС‹Р»РєРё. Р’С‹Р±РµСЂРёС‚Рµ РіСЂСѓРїРїСѓ:", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("admin:invite:create:"))
async def cb_admin_invite_create(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    gid = int(call.data.split(":")[-1])
    g = await db.get_group(gid)
    if not g:
        await call.answer("Р“СЂСѓРїРїР° РЅРµ РЅР°Р№РґРµРЅР°.", show_alert=True)
        return
    token = secrets.token_urlsafe(8)
    await db.create_invite(token, gid, tz_now(TZ_OFFSET_HOURS).isoformat())
    await call.message.edit_text(
        f"РЎСЃС‹Р»РєР° РґР»СЏ РіСЂСѓРїРїС‹ <b>{g['title']}</b>:\n"
        f"<code>https://t.me/{(await bot.me()).username}?start=g_{token}</code>",
        reply_markup=kb_back("admin:root"),
    )
    await call.answer()

# ----------- admin: slots root -----------# ----------- admin: slots root -----------

@router.callback_query(F.data == "admin:slots")

async def cb_admin_slots(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

        return

    await call.message.edit_text("Р—Р°РЅСЏС‚РёСЏ (СЃР»РѕС‚С‹):", reply_markup=kb_admin_slots_root())

    await call.answer()


# ----------- admin: tournaments root -----------
@router.callback_query(F.data == "admin:tournaments")
async def cb_admin_tournaments_root(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    await call.message.edit_text("РўСѓСЂРЅРёСЂС‹:", reply_markup=kb_admin_tournaments_root())
    await call.answer()

@router.callback_query(F.data == "admin:tournament:create")
async def cb_admin_tournament_create(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    await call.message.edit_text("Р’С‹Р±РµСЂРёС‚Рµ РіСЂСѓРїРїСѓ РґР»СЏ С‚СѓСЂРЅРёСЂР°:", reply_markup=kb_back("admin:tournaments"))
    await cb_admin_tournament_pickgroup(call, page=0)

@router.callback_query(F.data.startswith("admin:tournament:pickgroup:page:"))
async def cb_admin_tournament_pickgroup_cb(call: CallbackQuery):
    page = int(call.data.split(":")[-1])
    await cb_admin_tournament_pickgroup(call, page)

async def cb_admin_tournament_pickgroup(call: CallbackQuery, page: int):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
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
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ", callback_data=f"admin:tournament:pickgroup:page:{page-1}"))
    if offset + limit < total:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="вћЎпёЏ", callback_data=f"admin:tournament:pickgroup:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin:tournaments")])
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text("Р’С‹Р±РµСЂРёС‚Рµ РіСЂСѓРїРїСѓ РґР»СЏ С‚СѓСЂРЅРёСЂР°:", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("admin:tournament:create:group:"))
async def cb_admin_tournament_create_group(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    group_id = int(call.data.split(":")[-1])
    g = await db.get_group(group_id)
    if not g:
        await call.answer("Р“СЂСѓРїРїР° РЅРµ РЅР°Р№РґРµРЅР°.", show_alert=True)
        return
    draft = ADMIN_DRAFTS.setdefault(call.from_user.id, {"type": "tournament"})
    draft["group_id"] = group_id
    await db.set_mode(call.from_user.id, "admin_tournament_create:title")
    await call.message.edit_text(
        f"РЎРѕР·РґР°РЅРёРµ С‚СѓСЂРЅРёСЂР° РґР»СЏ РіСЂСѓРїРїС‹ <b>{g['title']}</b>.\n"
        "РЁР°Рі 1/5: РѕС‚РїСЂР°РІСЊС‚Рµ РЅР°Р·РІР°РЅРёРµ С‚СѓСЂРЅРёСЂР°.\n"
        "/cancel вЂ” РѕС‚РјРµРЅР°."
    )
    await call.answer()

@router.callback_query(F.data.startswith("admin:tournament:list:page:"))
async def cb_admin_tournament_list(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    page = int(call.data.split(":")[-1])
    limit = 10
    offset = page * limit
    total = await db.count_tournaments()
    tournaments = await db.list_tournaments(offset, limit)
    if not tournaments:
        await call.message.edit_text("РўСѓСЂРЅРёСЂРѕРІ РїРѕРєР° РЅРµС‚.", reply_markup=kb_back("admin:tournaments"))
        await call.answer()
        return
    rows = []
    for t in tournaments:
        dt = parse_dt(t["starts_at"])
        rows.append([__import__("aiogram").types.InlineKeyboardButton(
            text=f"{t['tournament_id']}. {t['title']} вЂ” {fmt_dt(dt)}",
            callback_data=f"admin:tournament:open:{t['tournament_id']}"
        )])
    nav = []
    if page > 0:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ", callback_data=f"admin:tournament:list:page:{page-1}"))
    if offset + limit < total:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="вћЎпёЏ", callback_data=f"admin:tournament:list:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin:tournaments")])
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text("РўСѓСЂРЅРёСЂС‹:", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("admin:tournament:open:"))
async def cb_admin_tournament_open(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    tournament_id = int(call.data.split(":")[-1])
    t = await db.get_tournament(tournament_id)
    if not t:
        await call.answer("РўСѓСЂРЅРёСЂ РЅРµ РЅР°Р№РґРµРЅ.", show_alert=True)
        return
    starts = parse_dt(t["starts_at"])
    booked = await db.count_active_bookings("tournament", tournament_id)
    waitlist_count = await db.count_bookings("tournament", tournament_id, "waitlist")
    text = (
        f"<b>РўСѓСЂРЅРёСЂ</b> #{tournament_id}\n"
        f"РќР°Р·РІР°РЅРёРµ: {t['title']}\n"
        f"рџ•’ {fmt_dt(starts)}\n"
        f"рџ‘Ґ РњРµСЃС‚: {booked}/{t['capacity']}\n"
        f"рџ“‹ Р›РёСЃС‚ РѕР¶РёРґР°РЅРёСЏ: {waitlist_count}/{t.get('waitlist_limit', 0)}\n"
    )
    if t.get("description"):
        text += f"\nрџ“ќ {t['description']}"
    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(text="рџ‘Ґ Р—Р°РїРёСЃР°РЅРЅС‹Рµ", callback_data=f"admin:tournament:{tournament_id}:users:page:0")],
        [__import__("aiogram").types.InlineKeyboardButton(text="вљ™пёЏ РќР°СЃС‚СЂРѕР№РєРё", callback_data=f"admin:tournament:{tournament_id}:settings")],
        [__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin:tournament:list:page:0")],
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
        lines.append(f"{i}) {it['full_name']} {uname} \u2014 {st}".strip())
        rows.append([__import__("aiogram").types.InlineKeyboardButton(
            text=f"{st} {it['full_name']}",
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

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

        return

    await cb_admin_slot_create_pickgroup(call, page=0)
    await call.answer()


@router.callback_query(F.data.startswith("admin:slot:create:pickgroup:page:"))

async def cb_admin_slot_create_pickgroup_page(call: CallbackQuery):

    page = int(call.data.split(":")[-1])

    await cb_admin_slot_create_pickgroup(call, page)


async def cb_admin_slot_create_pickgroup(call: CallbackQuery, page: int):

    if not is_admin(call.from_user.id):

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

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

        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ", callback_data=f"admin:slot:create:pickgroup:page:{page-1}"))

    if offset + limit < total:

        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="вћЎпёЏ", callback_data=f"admin:slot:create:pickgroup:page:{page+1}"))

    if nav:

        rows.append(nav)

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin:slots")])

    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text("РЎРѕР·РґР°РЅРёРµ СЃР»РѕС‚Р°: РІС‹Р±РµСЂРёС‚Рµ РіСЂСѓРїРїСѓ.", reply_markup=kb)

    await call.answer()


@router.callback_query(F.data.startswith("admin:slot:create:group:"))

async def cb_admin_slot_create_group(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

        return

    group_id = int(call.data.split(":")[-1])

    g = await db.get_group(group_id)

    if not g:

        await call.answer("Р“СЂСѓРїРїР° РЅРµ РЅР°Р№РґРµРЅР°.", show_alert=True)

        return

    draft = ADMIN_DRAFTS.setdefault(call.from_user.id, {"type": "slot"})

    draft["group_id"] = group_id

    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(text="РџРЅ", callback_data="admin:slot:create:weekday:0")],
        [__import__("aiogram").types.InlineKeyboardButton(text="Р’С‚", callback_data="admin:slot:create:weekday:1")],
        [__import__("aiogram").types.InlineKeyboardButton(text="РЎСЂ", callback_data="admin:slot:create:weekday:2")],
        [__import__("aiogram").types.InlineKeyboardButton(text="Р§С‚", callback_data="admin:slot:create:weekday:3")],
        [__import__("aiogram").types.InlineKeyboardButton(text="РџС‚", callback_data="admin:slot:create:weekday:4")],
        [__import__("aiogram").types.InlineKeyboardButton(text="РЎР±", callback_data="admin:slot:create:weekday:5")],
        [__import__("aiogram").types.InlineKeyboardButton(text="Р’СЃ", callback_data="admin:slot:create:weekday:6")],
        [__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin:slot:create:pickgroup:page:0")],
    ]

    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text(f"РЎРѕР·РґР°РЅРёРµ СЃР»РѕС‚Р° РґР»СЏ РіСЂСѓРїРїС‹ <b>{g['title']}</b>.\nР’С‹Р±РµСЂРёС‚Рµ РґРµРЅСЊ РЅРµРґРµР»Рё:", reply_markup=kb)

    await call.answer()


@router.callback_query(F.data.startswith("admin:slot:create:weekday:"))

async def cb_admin_slot_create_weekday(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

        return

    weekday = int(call.data.split(":")[-1])

    draft = ADMIN_DRAFTS.setdefault(call.from_user.id, {"type": "slot"})

    draft["weekday"] = weekday

    await db.set_mode(call.from_user.id, "admin_slot_create:time")

    await call.message.edit_text(
        "РЁР°Рі 2/3: РѕС‚РїСЂР°РІСЊС‚Рµ РІСЂРµРјСЏ РІ С„РѕСЂРјР°С‚Рµ HH:MM (РЅР°РїСЂРёРјРµСЂ 19:00).\n"
        "/cancel вЂ” РѕС‚РјРµРЅР°."
    )

    await call.answer()



@router.callback_query(F.data.startswith("admin:slot:pickgroup:page:"))

async def cb_admin_pickgroup(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

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

    if page>0: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ", callback_data=f"admin:slot:pickgroup:page:{page-1}"))

    if offset+limit<total: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="вћЎпёЏ", callback_data=f"admin:slot:pickgroup:page:{page+1}"))

    if nav: rows.append(nav)

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin:slots")])

    kb=__import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text("Р’С‹Р±РµСЂРёС‚Рµ РіСЂСѓРїРїСѓ:", reply_markup=kb)

    await call.answer()



@router.callback_query(F.data.startswith("admin:slot:list:"))

async def cb_admin_slot_list_for_group(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

        return

    gid=int(call.data.split(":")[-1])

    now=tz_now(TZ_OFFSET_HOURS)

    from_iso=(now - timedelta(days=1)).isoformat()

    to_iso=(now + timedelta(days=30)).isoformat()

    slots=await db.list_slots_for_group(gid, from_iso, to_iso, limit=30)

    if not slots:

        await call.message.edit_text("РЈ СЌС‚РѕР№ РіСЂСѓРїРїС‹ РЅРµС‚ СЃР»РѕС‚РѕРІ.", reply_markup=kb_back("admin:slots"))

        await call.answer()

        return

    rows=[]

    for s in slots[:15]:

        dt=parse_dt(s["starts_at"])

        rows.append([__import__("aiogram").types.InlineKeyboardButton(

            text=f"{fmt_dt(dt)}",

            callback_data=f"admin:slot:open:{s['slot_id']}"

        )])

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin:slots")])

    kb=__import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text(f"РЎР»РѕС‚С‹ РіСЂСѓРїРїС‹ {gid}:", reply_markup=kb)

    await call.answer()



@router.callback_query(F.data.startswith("admin:slot:open:"))

async def cb_admin_slot_open(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

        return

    slot_id=int(call.data.split(":")[-1])

    slot=await db.get_slot(slot_id)

    if not slot:

        await call.answer("РЎР»РѕС‚ РЅРµ РЅР°Р№РґРµРЅ.", show_alert=True)

        return

    starts=parse_dt(slot["starts_at"])

    booked=await db.count_active_bookings("training", slot_id)

    text=(

        f"<b>РЎР»РѕС‚</b> #{slot_id}\n"

        f"Р“СЂСѓРїРїР°: {slot['group_id']}\n"

        f"рџ•’ {fmt_dt(starts)}\n"

        f"рџ‘Ґ {booked}/{slot['capacity']}\n\n"

        f"РљРЅРѕРїРєРё РЅРёР¶Рµ: СЃРїРёСЃРѕРє Р·Р°РїРёСЃР°РЅРЅС‹С… (СЃ РѕРїР»Р°С‚РѕР№)."

    )

    # reuse message keyboard: open users list

    rows=[

        [__import__("aiogram").types.InlineKeyboardButton(text="рџ‘Ґ Р—Р°РїРёСЃР°РЅРЅС‹Рµ", callback_data=f"admin:training:{slot_id}:users:page:0")],

        [__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data=f"admin:slot:list:{slot['group_id']}")]

    ]

    kb=__import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text(text, reply_markup=kb)

    await call.answer()



@router.callback_query(F.data.startswith("admin:training:") & F.data.contains(":users:page:"))

async def cb_admin_training_users(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

        return

    parts=call.data.split(":")

    slot_id=int(parts[2])

    page=int(parts[-1])

    limit=15

    offset=page*limit

    total=await db.count_entity_bookings("training", slot_id)

    items=await db.list_entity_bookings("training", slot_id, offset, limit)

    lines=[f"<b>Р—Р°РїРёСЃР°РЅРЅС‹Рµ (СЃР»РѕС‚ #{slot_id})</b> ({total}):"]

    rows=[]

    for i, it in enumerate(items, start=offset+1):

        st="вњ…" if it.get("pay_status")=="confirmed" else "вЏі"

        uname=f"@{it['username']}" if it.get("username") else ""

        lines.append(f"{i}) {it['full_name']} {uname} вЂ” {st}".strip())

        rows.append([__import__("aiogram").types.InlineKeyboardButton(

            text=f"{st} {it['full_name']}",

            callback_data=f"admin:pay:toggle:{it['booking_id']}:{slot_id}:{page}"

        )])

    nav=[]

    if page>0: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ", callback_data=f"admin:training:{slot_id}:users:page:{page-1}"))

    if offset+limit<total: nav.append(__import__("aiogram").types.InlineKeyboardButton(text="вћЎпёЏ", callback_data=f"admin:training:{slot_id}:users:page:{page+1}"))

    if nav: rows.append(nav)

    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data=f"admin:slot:open:{slot_id}")])

    kb=__import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text("\n".join(lines), reply_markup=kb)

    await call.answer()



@router.callback_query(F.data.startswith("admin:pay:toggle:"))

async def cb_admin_pay_toggle(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)

        return

    # admin:pay:toggle:<booking_id>:<slot_id>:<page>

    _,_,_,booking_id, slot_id, page = call.data.split(":")

    new_status = await db.toggle_payment(int(booking_id), call.from_user.id)

    await call.answer("РћРїР»Р°С‚Р°: " + ("вњ… РїРѕРґС‚РІРµСЂР¶РґРµРЅР°" if new_status=="confirmed" else "вЏі РѕР¶РёРґР°РµС‚"))

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
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    s = await db.get_payment_settings()
    amount = s.get("amount")
    amount_text = f"РЎСѓРјРјР°: <b>{amount}</b>" if amount is not None else "РЎСѓРјРјР°: РЅРµ СѓРєР°Р·Р°РЅР°"
    text = (
        "<b>РћРїР»Р°С‚Р°: РЅР°СЃС‚СЂРѕР№РєРё</b>\n\n"
        f"РўРµРєСѓС‰РёР№ С‚РµРєСЃС‚:\n{s.get('text','')}\n\n"
        f"{amount_text}\n\n"
        "РСЃРїРѕР»СЊР·СѓР№С‚Рµ РєРЅРѕРїРєРё РЅРёР¶Рµ РґР»СЏ РёР·РјРµРЅРµРЅРёР№."
    )
    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(text="вњЌпёЏ РР·РјРµРЅРёС‚СЊ С‚РµРєСЃС‚", callback_data="admin:payset:edit")],
        [__import__("aiogram").types.InlineKeyboardButton(text="рџ’° РЈРєР°Р·Р°С‚СЊ СЃСѓРјРјСѓ", callback_data="admin:payset:amount")],
        [__import__("aiogram").types.InlineKeyboardButton(text="рџ§№ РЎР±СЂРѕСЃРёС‚СЊ РѕРїР»Р°С‚Сѓ", callback_data="admin:payset:reset")],
        [__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin:root")],
    ]
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "admin:payset:edit")
async def cb_admin_payset_edit(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    await db.set_mode(call.from_user.id, "admin_payset:text")
    await call.message.edit_text(
        "РћС‚РїСЂР°РІСЊС‚Рµ РЅРѕРІС‹Рј СЃРѕРѕР±С‰РµРЅРёРµРј С‚РµРєСЃС‚ РѕРїР»Р°С‚С‹.\n"
        "\/cancel вЂ” РѕС‚РјРµРЅР°",
        reply_markup=kb_back("admin:payset"),
    )
    await call.answer()

@router.callback_query(F.data == "admin:payset:amount")
async def cb_admin_payset_amount(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    await db.set_mode(call.from_user.id, "admin_payset:amount")
    await call.message.edit_text(
        "РћС‚РїСЂР°РІСЊС‚Рµ СЃСѓРјРјСѓ С‡РёСЃР»РѕРј (РЅР°РїСЂРёРјРµСЂ 3500).\n"
        "\/cancel вЂ” РѕС‚РјРµРЅР°",
        reply_markup=kb_back("admin:payset"),
    )
    await call.answer()

@router.callback_query(F.data == "admin:payset:reset")
async def cb_admin_payset_reset(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(text="вњ… Р”Р°, СЃР±СЂРѕСЃРёС‚СЊ", callback_data="admin:payset:reset:confirm")],
        [__import__("aiogram").types.InlineKeyboardButton(text="вќЊ РћС‚РјРµРЅР°", callback_data="admin:payset")],
    ]
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text(
        "Р’С‹ СѓРІРµСЂРµРЅС‹, С‡С‚Рѕ С…РѕС‚РёС‚Рµ СЃР±СЂРѕСЃРёС‚СЊ С‚РµРєСЃС‚ Рё СЃСѓРјРјСѓ РѕРїР»Р°С‚С‹?",
        reply_markup=kb,
    )
    await call.answer()

@router.callback_query(F.data == "admin:payset:reset:confirm")
async def cb_admin_payset_reset_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    await db.set_payment_settings("РћРїР»Р°С‚Р°: СѓС‚РѕС‡РЅРёС‚Рµ Сѓ С‚СЂРµРЅРµСЂР°.", None)
    await call.message.edit_text("РЎР±СЂРѕС€РµРЅРѕ.\nРўРµРєСЃС‚ Рё СЃСѓРјРјР° РѕРїР»Р°С‚С‹ РѕС‡РёС‰РµРЅС‹.", reply_markup=kb_back("admin:payset"))
    await call.answer()

@router.callback_query(F.data == "admin:bc")
async def cb_admin_bc(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    rows = [
        [__import__("aiogram").types.InlineKeyboardButton(text="рџ‘Ґ Р’СЃРµРј", callback_data="admin:bc:all")],
        [__import__("aiogram").types.InlineKeyboardButton(text="рџЋЇ Р’С‹Р±СЂР°С‚СЊ РіСЂСѓРїРїСѓ", callback_data="admin:bc:pickgroup:page:0")],
        [__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin:root")],
    ]
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text("Р Р°СЃСЃС‹Р»РєР°: РІС‹Р±РµСЂРёС‚Рµ РїРѕР»СѓС‡Р°С‚РµР»РµР№.", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "admin:bc:all")
async def cb_admin_bc_all(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    draft = ADMIN_DRAFTS.setdefault(call.from_user.id, {"type": "bc"})
    draft["target_gid"] = None
    await db.set_mode(call.from_user.id, "admin_bc:compose")
    await call.message.edit_text(
        "Р Р°СЃСЃС‹Р»РєР° РІСЃРµРј.\n"
        "РћС‚РїСЂР°РІСЊС‚Рµ СЃРѕРѕР±С‰РµРЅРёРµ СЃ С‚РµРєСЃС‚РѕРј.\n"
        "\/cancel вЂ” РѕС‚РјРµРЅР°",
        reply_markup=kb_back("admin:bc"),
    )
    await call.answer()

@router.callback_query(F.data.startswith("admin:bc:pickgroup:page:"))
async def cb_admin_bc_pickgroup_page(call: CallbackQuery):
    page = int(call.data.split(":")[-1])
    await cb_admin_bc_pickgroup(call, page)

async def cb_admin_bc_pickgroup(call: CallbackQuery, page: int):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    limit = 8
    offset = page * limit
    total = await db.count_groups()
    if total == 0:
        await call.message.edit_text(
            "Р“СЂСѓРїРї РїРѕРєР° РЅРµС‚. РЎРЅР°С‡Р°Р»Р° СЃРѕР·РґР°Р№С‚Рµ РіСЂСѓРїРїСѓ.",
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
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ", callback_data=f"admin:bc:pickgroup:page:{page-1}"))
    if offset + limit < total:
        nav.append(__import__("aiogram").types.InlineKeyboardButton(text="вћЎпёЏ", callback_data=f"admin:bc:pickgroup:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([__import__("aiogram").types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin:bc")])
    kb = __import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text("Р’С‹Р±РµСЂРёС‚Рµ РіСЂСѓРїРїСѓ РґР»СЏ СЂР°СЃСЃС‹Р»РєРё:", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("admin:bc:group:"))
async def cb_admin_bc_group(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    group_id = int(call.data.split(":")[-1])
    g = await db.get_group(group_id)
    if not g:
        await call.answer("Р“СЂСѓРїРїР° РЅРµ РЅР°Р№РґРµРЅР°.", show_alert=True)
        return
    draft = ADMIN_DRAFTS.setdefault(call.from_user.id, {"type": "bc"})
    draft["target_gid"] = group_id
    await db.set_mode(call.from_user.id, "admin_bc:compose")
    await call.message.edit_text(
        f"Р Р°СЃСЃС‹Р»РєР° РІ РіСЂСѓРїРїСѓ <b>{g['title']}</b>.\n"
        "РћС‚РїСЂР°РІСЊС‚Рµ СЃРѕРѕР±С‰РµРЅРёРµ СЃ С‚РµРєСЃС‚РѕРј.\n"
        "\/cancel вЂ” РѕС‚РјРµРЅР°",
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

        await message.answer("РћС‚РјРµРЅРµРЅРѕ.", reply_markup=kb_main(is_admin(message.from_user.id)))

        return



    # create group

    if mode == "admin_create_group:title":

        title = (message.text or "").strip()

        if not title:

            await message.answer("РџСѓСЃС‚Рѕ. Р’РІРµРґРёС‚Рµ РЅР°Р·РІР°РЅРёРµ РіСЂСѓРїРїС‹.")

            return

        gid = await db.create_group(title)

        await db.set_mode(message.from_user.id, None)

        await message.answer(f"Р“СЂСѓРїРїР° СЃРѕР·РґР°РЅР°. ID: <b>{gid}</b>", reply_markup=kb_admin_root())

        return



    # set group schedule photo

    if mode.startswith("admin_group_sched:"):

        group_id = int(mode.split(":")[1])

        if not message.photo:

            await message.answer("РќСѓР¶РЅР° РєР°СЂС‚РёРЅРєР° (С„РѕС‚Рѕ). РћС‚РїСЂР°РІСЊС‚Рµ С„РѕС‚Рѕ.")

            return

        file_id = message.photo[-1].file_id

        await db.set_group_schedule(group_id, file_id)

        await db.set_mode(message.from_user.id, None)

        await message.answer("Р Р°СЃРїРёСЃР°РЅРёРµ РѕР±РЅРѕРІР»РµРЅРѕ.", reply_markup=kb_admin_root())

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

                await message.answer("РќРµРІРµСЂРЅС‹Р№ С„РѕСЂРјР°С‚ РІСЂРµРјРµРЅРё. РџСЂРёРјРµСЂ: 19:00")

                return

            try:

                hh, mm = raw.split(":", 1)

                hour = int(hh)

                minute = int(mm)

                if hour < 0 or hour > 23 or minute < 0 or minute > 59:

                    raise ValueError

            except Exception:

                await message.answer("РќРµРІРµСЂРЅС‹Р№ С„РѕСЂРјР°С‚ РІСЂРµРјРµРЅРё. РџСЂРёРјРµСЂ: 19:00")

                return

            weekday = draft.get("weekday")

            if weekday is None:

                await message.answer("РќРµ РІС‹Р±СЂР°РЅ РґРµРЅСЊ РЅРµРґРµР»Рё. РќР°С‡РЅРёС‚Рµ Р·Р°РЅРѕРІРѕ.")

                return

            dt = next_weekday_datetime(int(weekday), raw)

            draft["starts_at"] = dt.isoformat()

            await db.set_mode(message.from_user.id, "admin_slot_create:capacity")

            await message.answer("РЁР°Рі 3/3: РѕС‚РїСЂР°РІСЊС‚Рµ Р»РёРјРёС‚ РјРµСЃС‚ (С‡РёСЃР»Рѕ). РњРѕР¶РЅРѕ СЃ РїСЂРёРјРµС‡Р°РЅРёРµРј: 6;РўСЂРµРЅРёСЂРѕРІРєР° РІ Р·Р°Р»Рµ")

            return

        if step == "capacity":

            raw=(message.text or "").strip()

            note=None

            if ";" in raw:

                cap_s, note = raw.split(";",1)

                raw=cap_s.strip()

                note=note.strip() or None

            if not raw.isdigit():

                await message.answer("РќСѓР¶РЅРѕ С‡РёСЃР»Рѕ. РџСЂРёРјРµСЂ: 6 РёР»Рё 6;РџСЂРёРјРµС‡Р°РЅРёРµ")

                return

            cap=int(raw)

            slot_id = await db.create_slot(draft["group_id"], draft["starts_at"], cap, note)

            ADMIN_DRAFTS.pop(message.from_user.id, None)

            await db.set_mode(message.from_user.id, None)

            await message.answer(f"РЎР»РѕС‚ СЃРѕР·РґР°РЅ: #{slot_id}", reply_markup=kb_admin_root())

            return



    

    # tournament create multi-step
    if mode.startswith("admin_tournament_create:"):
        step = mode.split(":")[1]
        draft = ADMIN_DRAFTS.setdefault(message.from_user.id, {"type": "tournament"})

        if step == "title":
            title = (message.text or "").strip()
            if not title:
                await message.answer("РџСѓСЃС‚Рѕ. Р’РІРµРґРёС‚Рµ РЅР°Р·РІР°РЅРёРµ С‚СѓСЂРЅРёСЂР°.")
                return
            draft["title"] = title
            await db.set_mode(message.from_user.id, "admin_tournament_create:starts_at")
            await message.answer("РЁР°Рі 2/5: РѕС‚РїСЂР°РІСЊС‚Рµ РґР°С‚Сѓ/РІСЂРµРјСЏ РІ С„РѕСЂРјР°С‚Рµ YYYY-MM-DD HH:MM (РЅР°РїСЂРёРјРµСЂ 2026-01-30 19:00)")
            return

        if step == "starts_at":
            raw = (message.text or "").strip()
            try:
                from datetime import datetime
                dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
                dt = dt.replace(tzinfo=tz_now(TZ_OFFSET_HOURS).tzinfo)
            except Exception:
                await message.answer("РќРµРІРµСЂРЅС‹Р№ С„РѕСЂРјР°С‚. РџСЂРёРјРµСЂ: 2026-01-30 19:00")
                return
            draft["starts_at"] = dt.isoformat()
            await db.set_mode(message.from_user.id, "admin_tournament_create:capacity")
            await message.answer("РЁР°Рі 3/5: РѕС‚РїСЂР°РІСЊС‚Рµ Р»РёРјРёС‚ РјРµСЃС‚ (С‡РёСЃР»Рѕ).")
            return

        if step == "capacity":
            raw = (message.text or "").strip()
            if not raw.isdigit():
                await message.answer("РќСѓР¶РЅРѕ С‡РёСЃР»Рѕ. РџСЂРёРјРµСЂ: 16")
                return
            draft["capacity"] = int(raw)
            await db.set_mode(message.from_user.id, "admin_tournament_create:waitlist")
            await message.answer("РЁР°Рі 4/5: Р»РёРјРёС‚ Р»РёСЃС‚Р° РѕР¶РёРґР°РЅРёСЏ (С‡РёСЃР»Рѕ, 0 = Р±РµР· Р»РёСЃС‚Р° РѕР¶РёРґР°РЅРёСЏ).")
            return

        if step == "waitlist":
            raw = (message.text or "").strip()
            if not raw.isdigit():
                await message.answer("РќСѓР¶РЅРѕ С‡РёСЃР»Рѕ. РџСЂРёРјРµСЂ: 10 РёР»Рё 0")
                return
            draft["waitlist_limit"] = int(raw)
            await db.set_mode(message.from_user.id, "admin_tournament_create:description")
            await message.answer("РЁР°Рі 5/5: РѕРїРёСЃР°РЅРёРµ (РёР»Рё РѕС‚РїСЂР°РІСЊС‚Рµ '-' С‡С‚РѕР±С‹ РїСЂРѕРїСѓСЃС‚РёС‚СЊ).")
            return

        if step == "description":
            raw = (message.text or "").strip()
            desc = None if raw in ("-", "") else raw
            draft["description"] = desc

            group_id = draft.get("group_id")
            if not group_id:
                await message.answer("РќРµ РІС‹Р±СЂР°РЅР° РіСЂСѓРїРїР°.")
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
                f"РўСѓСЂРЅРёСЂ СЃРѕР·РґР°РЅ: #{tournament_id}\n"
                "Р—Р°РїРёСЃСЊ РѕС‚РєСЂС‹С‚Р° СЃСЂР°Р·Сѓ. Р—Р°РєСЂС‹С‚РёРµ вЂ” РїРѕ РЅР°СЃС‚СЂРѕР№РєР°Рј РіСЂСѓРїРїС‹.",
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

            await message.answer("РџСѓСЃС‚РѕР№ С‚РµРєСЃС‚.")

            return

        amount=None

        lines=[]

        for line in txt.splitlines():

            if line.strip().startswith("amount="):

                try:

                    amount=int(line.split("=",1)[1].strip())

                except Exception:

                    await message.answer("amount РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ С‡РёСЃР»РѕРј.")

                    return

            else:

                lines.append(line)

        final="\n".join(lines).strip() or "РћРїР»Р°С‚Р°: СѓС‚РѕС‡РЅРёС‚Рµ Сѓ С‚СЂРµРЅРµСЂР°."

        await db.set_payment_settings(final, amount)

        await db.set_mode(message.from_user.id, None)

        await message.answer("\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u043e\u043f\u043b\u0430\u0442\u044b \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b.", reply_markup=kb_back("admin:payset"))

        return



    if mode == "admin_payset:amount":
        raw = (message.text or "").strip()
        if not raw.isdigit():
            await message.answer("РЎСѓРјРјР° РґРѕР»Р¶РЅР° Р±С‹С‚СЊ С‡РёСЃР»РѕРј.")
            return
        amount = int(raw)
        s = await db.get_payment_settings()
        text_val = s.get("text", "")
        await db.set_payment_settings(text_val, amount)
        await db.set_mode(message.from_user.id, None)
        await message.answer("\u0421\u0443\u043c\u043c\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0430.", reply_markup=kb_back("admin:payset"))
        return


    # broadcast
    if mode == "admin_bc:compose":
        txt = (message.text or "").strip()
        if not txt:
            await message.answer("РџСѓСЃС‚РѕР№ С‚РµРєСЃС‚.")
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
        await message.answer(f"Р Р°СЃСЃС‹Р»РєР° РѕС‚РїСЂР°РІР»РµРЅР°: {sent}", reply_markup=kb_admin_root())
        return


# ---------------- main ----------------

async def main():

    await db.init()
    for uid in ADMIN_IDS:
        await db.add_admin(uid)
    global ADMIN_CACHE
    ADMIN_CACHE = set(await db.list_admins()) | set(ADMIN_IDS)

    logger.info("DB initialized")

    await dp.start_polling(bot)



if __name__ == "__main__":

    asyncio.run(main())










