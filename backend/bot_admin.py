import asyncio
import re
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Message
from psycopg.errors import UniqueViolation

from .crew import MAIN_ADMIN_MEMBER
from .crew_store import CrewError, bot_actor, connect, crew_details, lock_crew, update_account, update_crew
from .telegram_auth import MAX_TELEGRAM_ID

router = Router(name="crew-admin")
PAGE_SIZE = 8
ADD_PROMPT = "Ответьте на это сообщение числовым Telegram ID для белого списка."


@dataclass
class View:
    text: str
    buttons: list[list[tuple[str, str]]]

    def markup(self):
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"admin:{action}") for label, action in row]
            for row in self.buttons
        ])


def identifier(raw):
    if not re.fullmatch(r"[0-9]{1,16}", raw) or not 1 <= int(raw) <= MAX_TELEGRAM_ID:
        raise CrewError(422, "Telegram ID должен быть положительным числом")
    return int(raw)


def account(conn, telegram_id):
    row = conn.execute(
        "SELECT * FROM telegram_accounts WHERE telegram_id = %s", (telegram_id,),
    ).fetchone()
    if not row:
        raise CrewError(404, "ID не найден. Добавьте его в белый список.")
    return row


def member(conn, member_id):
    row = conn.execute(
        "SELECT id,name,vessel,hidden,telegram_id FROM crew WHERE id = %s", (member_id,),
    ).fetchone()
    if not row:
        raise CrewError(404, "Персонаж не найден")
    return row


def menu(conn, actor):
    buttons = [[(f"{'◌ ' if m['hidden'] else ''}{m['name']}", f"crew:{m['id']}")]
               for m in crew_details(conn, False)]
    if actor["mainAdmin"]:
        buttons.append([("Белый список и админы", "accounts:0")])
    return View("Управление командой «Хамамтона»", buttons)


def crew_view(conn, actor, member_id):
    m = member(conn, member_id)
    vessel = "Хамамтон" if m["vessel"] == "ship" else "Гнилая шлюпка"
    text = f"{m['name']} · {vessel}\n{'Скрыт из команды' if m['hidden'] else 'Виден в команде'}"
    buttons = [
        [("Пересадить в шлюпку", f"vessel:{member_id}:dinghy") if m["vessel"] == "ship"
         else ("Вернуть на Хамамтон", f"vessel:{member_id}:ship")],
        [("Показать персонажа" if m["hidden"] else "Скрыть персонажа", f"hide:{member_id}:{int(not m['hidden'])}")],
    ]
    if actor["mainAdmin"] and member_id != MAIN_ADMIN_MEMBER:
        text += f"\nTelegram ID: {m['telegram_id'] or 'не назначен'}"
        buttons.append([("Назначить Telegram ID", f"bind:{member_id}:0")])
        if m["telegram_id"]:
            buttons.append([("Отвязать Telegram ID", f"unbind:{member_id}")])
    return View(text, buttons + [[("К команде", "menu")]])


def account_view(conn, telegram_id, owner):
    a = account(conn, telegram_id)
    role = "Главный админ" if telegram_id == owner else "Админ" if a["is_admin"] else "Участник"
    buttons = []
    if telegram_id != owner:
        buttons = [
            [("Отозвать доступ" if a["allowed"] else "Разрешить доступ", f"access:{telegram_id}:{int(not a['allowed'])}")],
            [("Снять роль админа" if a["is_admin"] else "Назначить админом", f"role:{telegram_id}:{int(not a['is_admin'])}")],
        ]
    return View(f"Telegram ID {telegram_id}\n{role}\nДоступ: {'да' if a['allowed'] else 'нет'}",
                buttons + [[("К списку ID", "accounts:0")]])


def account_list(conn, page, member_id=None):
    if not 0 <= page <= 10000:
        raise CrewError(422, "Неверная страница")
    forward = f"bind:{member_id}" if member_id is not None else "accounts"
    rows = conn.execute("""
        SELECT a.telegram_id, a.allowed, a.is_admin, c.name
        FROM telegram_accounts a LEFT JOIN crew c USING (telegram_id)
        WHERE (%s OR (a.allowed AND c.id IS NULL))
        ORDER BY a.telegram_id LIMIT %s OFFSET %s
    """, (member_id is None, PAGE_SIZE + 1, page * PAGE_SIZE)).fetchall()
    buttons = []
    for row in rows[:PAGE_SIZE]:
        tid = row["telegram_id"]
        action = f"assign:{member_id}:{tid}" if member_id is not None else f"account:{tid}"
        label = str(tid)
        if row["name"]:
            label += f" · {row['name']}"
        if row["is_admin"]:
            label += " · админ"
        if not row["allowed"]:
            label += " · закрыт"
        buttons.append([(label, action)])
    navigation = []
    if page:
        navigation.append(("‹", f"{forward}:{page - 1}"))
    if len(rows) > PAGE_SIZE:
        navigation.append(("›", f"{forward}:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    if member_id is None:
        buttons.append([("Добавить Telegram ID", "add")])
        text = "Белый список и роли Telegram"
    else:
        text = f"Выберите свободный ID для «{member(conn, member_id)['name']}»."
        if not rows:
            text += "\nСвободных ID с доступом нет. Добавьте ID через белый список."
    return View(text, buttons + [[("Назад", "menu" if member_id is None else f"crew:{member_id}")]])


def admin_action(uid, owner, action):
    with connect("prod") as conn:
        lock_crew(conn)
        actor = bot_actor(conn, uid, owner)
        if not actor or not actor["admin"]:
            return None
        command, *args = action.split(":")
        if command == "menu" and not args:
            return menu(conn, actor)
        if command in ("crew", "vessel", "hide") and len(args) == (1 if command == "crew" else 2):
            member_id = int(args[0])
            if command == "vessel" and args[1] in ("ship", "dinghy"):
                update_crew(conn, actor, member_id, {"vessel": args[1]})
            if command == "hide" and args[1] in ("0", "1"):
                update_crew(conn, actor, member_id, {"hidden": args[1] == "1"})
            return crew_view(conn, actor, member_id)
        if not actor["mainAdmin"]:
            return None
        if command == "accounts" and len(args) == 1:
            return account_list(conn, int(args[0]))
        if command == "add" and not args:
            return ADD_PROMPT
        if command == "register" and len(args) == 1:
            tid = identifier(args[0])
            if tid != owner:
                update_account(conn, actor, tid, owner, allowed=True)
            return account_view(conn, tid, owner)
        if command in ("account", "access", "role") and len(args) == (1 if command == "account" else 2):
            tid = identifier(args[0])
            if command != "account" and args[1] in ("0", "1"):
                account(conn, tid)
                field = "allowed" if command == "access" else "is_admin"
                update_account(conn, actor, tid, owner, **{field: args[1] == "1"})
            return account_view(conn, tid, owner)
        if command in ("bind", "assign", "unbind") and len(args) == (1 if command == "unbind" else 2):
            member_id = int(args[0])
            if member_id == MAIN_ADMIN_MEMBER:
                raise CrewError(403, "Привязка Мыши задаётся в настройках")
            if command == "bind":
                return account_list(conn, int(args[1]), member_id)
            tid = identifier(args[1]) if command == "assign" else None
            if tid is not None and not account(conn, tid)["allowed"]:
                raise CrewError(422, "Сначала разрешите доступ этому ID")
            update_crew(conn, actor, member_id, {"telegram_id": tid})
            return crew_view(conn, actor, member_id)
    return None


async def execute(uid, owner, action):
    try:
        return await asyncio.to_thread(admin_action, uid, owner, action)
    except UniqueViolation:
        return View("ID уже назначен другому персонажу. Сначала отвяжите его.", [[("К команде", "menu")]])
    except CrewError as exc:
        return View(exc.detail, [[("К команде", "menu")]])
    except ValueError:
        return None


@router.message(Command("admin"), F.chat.type == ChatType.PRIVATE)
async def admin_command(message: Message, owner_id: int):
    if not message.from_user:
        return
    view = await execute(message.from_user.id, owner_id, "menu")
    if isinstance(view, View):
        await message.answer(view.text, reply_markup=view.markup())


@router.callback_query(F.data.startswith("admin:"))
async def admin_button(query: CallbackQuery, owner_id: int):
    message = query.message
    if not isinstance(message, Message) or message.chat.type != ChatType.PRIVATE or not query.data:
        await query.answer()
        return
    action = query.data.removeprefix("admin:")
    # The register action is accepted only from a direct reply to our ID prompt.
    if action == "register" or action.startswith("register:"):
        await query.answer()
        return
    view = await execute(query.from_user.id, owner_id, action)
    await query.answer()
    if isinstance(view, str):
        await message.answer(view, reply_markup=ForceReply(selective=True))
    elif view and (message.text != view.text or message.reply_markup != view.markup()):
        await message.edit_text(view.text, reply_markup=view.markup())


@router.message(F.chat.type == ChatType.PRIVATE, F.reply_to_message.text == ADD_PROMPT, F.text)
async def register_reply(message: Message, owner_id: int):
    replied = message.reply_to_message
    if not message.from_user or not replied or not replied.from_user or replied.from_user.id != message.bot.id:
        return
    value = (message.text or "").strip()
    # Keep the dispatcher action format independent of user input delimiters.
    if ":" in value:
        value = ""
    view = await execute(message.from_user.id, owner_id, f"register:{value}")
    if isinstance(view, View):
        await message.answer(view.text, reply_markup=view.markup())
