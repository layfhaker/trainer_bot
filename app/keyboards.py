from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_main(is_admin: bool):
    rows = [
        [InlineKeyboardButton(text="🟩 Запись на занятия", callback_data="train:list")],
        [InlineKeyboardButton(text="🗓 Расписание", callback_data="sched:show")],
        [InlineKeyboardButton(text="🏆 Турниры", callback_data="tour:list")],
        [InlineKeyboardButton(text="💳 Оплата", callback_data="pay:info")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 Админ меню", callback_data="admin:root")])
    return ikb(rows)

def kb_back(to: str = "main"):
    return ikb([[InlineKeyboardButton(text="⬅️ Назад", callback_data=to)]])

def kb_admin_root():
    return ikb([
        [InlineKeyboardButton(text="👥 Группы", callback_data="admin:groups:page:0")],
        [InlineKeyboardButton(text="🔗 Пригласительные ссылки", callback_data="admin:invites")],
        [InlineKeyboardButton(text="📅 Занятия (слоты)", callback_data="admin:slots")],
        [InlineKeyboardButton(text="💳 Оплата: реквизиты", callback_data="admin:payset")],
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:bc")],
        [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="main")],
    ])

def kb_pagination(prefix: str, page: int, has_prev: bool, has_next: bool, extra_buttons=None):
    extra_buttons = extra_buttons or []
    nav=[]
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}:page:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}:page:{page+1}"))
    rows=[]
    if nav:
        rows.append(nav)
    rows.extend(extra_buttons)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:root")])
    return ikb(rows)

def kb_group_actions(group_id: int):
    return ikb([
        [InlineKeyboardButton(text="🖼 Загрузить расписание", callback_data=f"admin:group:{group_id}:sched")],
        [InlineKeyboardButton(text="⚙️ Настройки записи/отмены", callback_data=f"admin:group:{group_id}:settings")],
        [InlineKeyboardButton(text="👤 Ученики", callback_data=f"admin:group:{group_id}:users:page:0")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:groups:page:0")],
    ])

def kb_slot_actions(slot_id: int, can_join: bool, can_leave: bool):
    rows=[]
    if can_join:
        rows.append([InlineKeyboardButton(text="✅ Записаться", callback_data=f"train:join:{slot_id}")])
    if can_leave:
        rows.append([InlineKeyboardButton(text="❌ Отменить запись", callback_data=f"train:leave:{slot_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="train:list")])
    return ikb(rows)

def kb_admin_slots_root():
    return ikb([
        [InlineKeyboardButton(text="➕ Создать слот", callback_data="admin:slot:create")],
        [InlineKeyboardButton(text="📄 Слоты по группам", callback_data="admin:slot:pickgroup:page:0")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:root")],
    ])

def kb_admin_entity_users(entity_type: str, entity_id: int, page: int, has_prev: bool, has_next: bool):
    nav=[]
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin:{entity_type}:{entity_id}:users:page:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin:{entity_type}:{entity_id}:users:page:{page+1}"))
    rows=[]
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:slots")])
    return ikb(rows)
