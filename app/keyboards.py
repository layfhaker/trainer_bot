from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_main(is_admin: bool):
    rows = [
        [InlineKeyboardButton(text="🟩 Запись на занятия", callback_data="train:list")],
        [InlineKeyboardButton(text="🗓 Расписание", callback_data="sched:show")],
        [InlineKeyboardButton(text="🏆 Турниры", callback_data="tour:list")],
        [InlineKeyboardButton(text="💳 Оплата", callback_data="pay:info")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="user:settings")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 Админ меню", callback_data="admin:root")])
    return ikb(rows)


def kb_back(to: str = "main"):
    return ikb([[InlineKeyboardButton(text="⬅️ Назад", callback_data=to)]])


def kb_admin_root():
    return ikb([
        [InlineKeyboardButton(text="👥 Группы", callback_data="admin:groups:page:0")],
        [InlineKeyboardButton(text="👥 Общие группы", callback_data="admin:commongroups")],
        [InlineKeyboardButton(text="🔗 Пригласительные ссылки", callback_data="admin:invites")],
        [InlineKeyboardButton(text="🔑 Пригласить админа", callback_data="admin:invite_admin")],
        [InlineKeyboardButton(text="📅 Занятия (слоты)", callback_data="admin:slots")],
        [InlineKeyboardButton(text="🏆 Турниры", callback_data="admin:tournaments")],
        [InlineKeyboardButton(text="💳 Оплата: реквизиты", callback_data="admin:payset")],
        [InlineKeyboardButton(text="🔔 Оповещения", callback_data="admin:notifyset")],
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:bc")],
        [InlineKeyboardButton(text="🧹 Сбросить всё", callback_data="admin:reset")],
        [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="main")],
    ])


def kb_pagination(prefix: str, page: int, has_prev: bool, has_next: bool, extra_buttons=None):
    extra_buttons = extra_buttons or []
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}:page:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}:page:{page+1}"))
    rows = []
    if nav:
        rows.append(nav)
    rows.extend(extra_buttons)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:root")])
    return ikb(rows)


def kb_group_actions(group_id: int):
    return ikb([
        [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"admin:group:{group_id}:title")],
        [InlineKeyboardButton(text="🖼 Загрузить расписание", callback_data=f"admin:group:{group_id}:sched")],
        [InlineKeyboardButton(text="⚙️ Настройки записи/отмены", callback_data=f"admin:group:{group_id}:settings")],
        [InlineKeyboardButton(text="👤 Ученики", callback_data=f"admin:group:{group_id}:users:page:0")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:groups:page:0")],
    ])


def kb_slot_actions(
    slot_id: int,
    can_join: bool,
    can_leave: bool,
    can_join_second: bool = False,
    can_admin_book: bool = False,
    show_users_button: bool = True,
    can_increase_capacity: bool = False,
):
    rows = []
    if can_join:
        rows.append([InlineKeyboardButton(text="✅ Записаться", callback_data=f"train:join:{slot_id}")])
    if can_join_second:
        rows.append([InlineKeyboardButton(text="👥 Записать второго человека", callback_data=f"train:join2:{slot_id}")])
    if can_admin_book:
        rows.append([InlineKeyboardButton(text="➕ Записать человека", callback_data=f"admin:training:book:{slot_id}:user")])
    if can_increase_capacity:
        rows.append([InlineKeyboardButton(text="➕ Увеличить места", callback_data=f"admin:slot:capadd:{slot_id}:train")])
    if show_users_button:
        rows.append([InlineKeyboardButton(text="👥 Записанные", callback_data=f"train:users:{slot_id}:page:0")])
    if can_leave:
        rows.append([InlineKeyboardButton(text="❌ Отменить запись", callback_data=f"train:leave:{slot_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="train:list")])
    return ikb(rows)


def kb_tour_actions(tournament_id: int, can_join: bool, can_leave: bool, is_waitlist: bool, can_join_second: bool = False):
    rows = []
    if can_join:
        rows.append([InlineKeyboardButton(text="✅ Записаться", callback_data=f"tour:join:{tournament_id}")])
    if can_join_second:
        rows.append([InlineKeyboardButton(text="👥 Записать второго человека", callback_data=f"tour:join2:{tournament_id}")])
    if can_leave:
        text = "❌ Выйти из листа ожидания" if is_waitlist else "❌ Отменить запись"
        rows.append([InlineKeyboardButton(text=text, callback_data=f"tour:leave:{tournament_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="tour:list")])
    return ikb(rows)


def kb_admin_slots_root():
    return ikb([
        [InlineKeyboardButton(text="➕ Создать слот", callback_data="admin:slot:create")],
        [InlineKeyboardButton(text="📄 Слоты по группам", callback_data="admin:slot:pickgroup:page:0")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:root")],
    ])


def kb_admin_tournaments_root():
    return ikb([
        [InlineKeyboardButton(text="➕ Создать турнир", callback_data="admin:tournament:create")],
        [InlineKeyboardButton(text="📄 Список турниров", callback_data="admin:tournament:list:page:0")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:root")],
    ])


def kb_admin_common_groups(groups, page: int, has_prev: bool, has_next: bool):
    rows = []
    for g in groups:
        mark = "✅" if g.get("chat_id") else "⚪"
        title = g.get("title") or f"Группа {g['group_id']}"
        rows.append([InlineKeyboardButton(text=f"{mark} {title}", callback_data=f"admin:commongroup:{g['group_id']}:{page}")])
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin:commongroups:page:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin:commongroups:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:root")])
    return ikb(rows)


def kb_admin_select_chat(group_id: int, chats, page: int, has_prev: bool, has_next: bool, has_unlink: bool = True):
    rows = []
    for ch in chats:
        title = ch.get("title") or str(ch["chat_id"])
        rows.append([InlineKeyboardButton(text=title, callback_data=f"admin:commongroupchat:{group_id}:{ch['chat_id']}:{page}")])
    if has_unlink:
        rows.append([InlineKeyboardButton(text="❌ Убрать привязку", callback_data=f"admin:commongroupchat:{group_id}:none:{page}")])
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin:commongroup:{group_id}:page:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin:commongroup:{group_id}:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:commongroups:page:0")])
    return ikb(rows)


def kb_admin_entity_users(entity_type: str, entity_id: int, page: int, has_prev: bool, has_next: bool, back_to: str):
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin:{entity_type}:{entity_id}:users:page:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin:{entity_type}:{entity_id}:users:page:{page+1}"))
    rows = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_to)])
    return ikb(rows)
