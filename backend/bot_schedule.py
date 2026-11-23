import asyncio
import logging
from datetime import date, datetime, time, timedelta

import psycopg
from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import AiogramError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from backend.crew_store import connect
from backend.schedule_store import read_schedule
from backend.timetable import MINSK, SATURDAY, WEEKDAY, schedule_days

logger = logging.getLogger(__name__)
router = Router(name="schedule")
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)

SUBGROUPS = {"set_sub_0": 0, "set_sub_1": 1, "set_sub_2": 2}
SUBGROUP_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="1-я подгруппа", callback_data="set_sub_1"),
     InlineKeyboardButton(text="2-я подгруппа", callback_data="set_sub_2")],
    [InlineKeyboardButton(text="Вся группа", callback_data="set_sub_0")],
])
ACCESS_DENIED = "У вас нет доступа к расписанию."


def subgroup_title(subgroup: int) -> str:
    return f"{subgroup}-я подгруппа" if subgroup else "Вся группа"


def matching_lessons(rows: list[dict], subgroup: int) -> list[dict]:
    return sorted(
        (row for row in rows if not subgroup or row["subgroup"] in (0, subgroup)),
        key=lambda row: (row["lesson_num"], row["subgroup"]),
    )


def lesson_line(row: dict) -> str:
    subgroup = f" (подгр. {row['subgroup']})" if row["subgroup"] else ""
    room = f"каб. {row['auditorium']}" if row["auditorium"] else "каб. не указан"
    return f"• {row['lesson_num']}. {row['subject']}{subgroup} — {room}"


def format_schedule(today: date, next_day: bool, subgroup: int, documents: dict[date, list[dict]]) -> str:
    target, label = today, "сегодня"
    if next_day:
        candidates = schedule_days(today)[1:]
        target = next((day for day in candidates if day in documents), candidates[-1])
        label = "следующий день"
    heading = f"📅 Расписание на {label} ({target.isoformat()})"
    if target not in documents:
        return f"{heading}\nРасписание пока не опубликовано."
    rows = matching_lessons(documents[target], subgroup)
    lessons = "\n".join(map(lesson_line, rows)) if rows else "Пар для вашей подгруппы нет."
    return f"{heading}\nПрофиль: {subgroup_title(subgroup)}\n\n{lessons}"


def account_subgroup(telegram_id: int, *, start: bool = False) -> int | None:
    with connect("prod") as conn:
        if start:
            row = conn.execute("""
                UPDATE telegram_accounts SET started = true
                WHERE telegram_id = %s AND allowed RETURNING subgroup
            """, (telegram_id,)).fetchone()
        else:
            row = conn.execute("""
                SELECT subgroup FROM telegram_accounts WHERE telegram_id = %s AND allowed
            """, (telegram_id,)).fetchone()
    return row["subgroup"] if row is not None else None


def save_subgroup(telegram_id: int, subgroup: int) -> bool:
    with connect("prod") as conn:
        return bool(conn.execute("""
            UPDATE telegram_accounts SET subgroup = %s WHERE telegram_id = %s AND allowed
        """, (subgroup, telegram_id)).rowcount)


def read_for_account(telegram_id: int, today: date, next_day: bool) -> str:
    with connect("prod") as conn:
        row = conn.execute("""
            SELECT subgroup FROM telegram_accounts WHERE telegram_id = %s AND allowed
        """, (telegram_id,)).fetchone()
        if row is None:
            return ACCESS_DENIED
        documents = read_schedule(conn, schedule_days(today)[1:] if next_day else (today,))
    return format_schedule(today, next_day, row["subgroup"], documents)


@router.message(Command("start", "subgroup"))
async def choose_subgroup(message: Message, command: CommandObject):
    if message.from_user is None:
        return
    subgroup = await asyncio.to_thread(account_subgroup, message.from_user.id, start=command.command == "start")
    if subgroup is None:
        await message.answer(ACCESS_DENIED)
        return
    await message.answer(
        f"Выберите подгруппу для расписания и уведомлений. Сейчас: {subgroup_title(subgroup)}.",
        reply_markup=SUBGROUP_KEYBOARD,
    )


@router.callback_query(F.data.in_(SUBGROUPS))
async def set_subgroup(callback: CallbackQuery):
    subgroup = SUBGROUPS[callback.data]
    saved = await asyncio.to_thread(save_subgroup, callback.from_user.id, subgroup)
    await callback.answer(f"Сохранено: {subgroup_title(subgroup)}" if saved else ACCESS_DENIED, show_alert=True)


@router.message(Command("today", "nextday"))
async def send_schedule(message: Message, command: CommandObject):
    if message.from_user is None:
        return
    text = await asyncio.to_thread(
        read_for_account, message.from_user.id, datetime.now(MINSK).date(), command.command == "nextday",
    )
    await message.answer(text, parse_mode=None)


def due_bell(now: datetime) -> tuple[int, str] | None:
    local = now.astimezone(MINSK)
    if local.weekday() == 6:
        return None
    grid = SATURDAY if local.weekday() == 5 else WEEKDAY
    bells = (grid[0][0], *(end for _, end in grid))
    due = (local + timedelta(minutes=15)).strftime("%H:%M")
    return next(((slot, bell) for slot, bell in enumerate(bells) if bell == due), None)


def format_alert(rows: list[dict], subgroup: int, slot: int, bell: str) -> str | None:
    lessons = matching_lessons(rows, subgroup)
    if not lessons or not lessons[0]["lesson_num"] - 1 <= slot <= lessons[-1]["lesson_num"]:
        return None
    if slot == lessons[-1]["lesson_num"]:
        return f"🔔 В {bell} заканчиваются уроки."
    heading = f"⏳ В {bell} начинается {'1-й урок' if slot == 0 else 'перемена'}."
    upcoming = [lesson_line(row) for row in lessons if row["lesson_num"] == slot + 1]
    details = "\n".join(upcoming) if upcoming else f"Перерыв: {slot + 1}-й пары у вас нет."
    return f"{heading}\n\n{details}"


def notification_data(day: date) -> tuple[list[dict], list[dict]]:
    with connect("prod") as conn:
        conn.execute("DELETE FROM public.bot_notifications WHERE day < %s", (day - timedelta(days=7),))
        rows = read_schedule(conn, (day,)).get(day, [])
        accounts = conn.execute("""
            SELECT telegram_id, subgroup FROM telegram_accounts WHERE allowed AND started
        """).fetchall() if rows else []
    return rows, accounts


def claim_notification(day: date, slot: int, telegram_id: int, subgroup: int) -> bool:
    with connect("prod") as conn:
        return bool(conn.execute("""
            INSERT INTO public.bot_notifications (day, slot, telegram_id)
            SELECT %s, %s, telegram_id FROM telegram_accounts
            WHERE telegram_id = %s AND subgroup = %s AND allowed AND started
            ON CONFLICT (day, slot, telegram_id) DO NOTHING
        """, (day, slot, telegram_id, subgroup)).rowcount)


async def send_notifications(bot: Bot, now: datetime, slot: int, bell: str):
    day = now.date()
    deadline = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    rows, accounts = await asyncio.to_thread(notification_data, day)
    for account in accounts:
        if datetime.now(MINSK) >= deadline:
            return
        telegram_id, subgroup = account["telegram_id"], account["subgroup"]
        text = format_alert(rows, subgroup, slot, bell)
        if not text or not await asyncio.to_thread(claim_notification, day, slot, telegram_id, subgroup):
            continue
        # Telegram cannot deduplicate sends whose outcome is unknown after a timeout or restart.
        try:
            await asyncio.wait_for(bot.send_message(telegram_id, text, parse_mode=None), timeout=10)
        except (AiogramError, TimeoutError) as exc:
            logger.warning("Schedule reminder for slot %s failed (%s)", slot, type(exc).__name__)
        await asyncio.sleep(0.05)


async def notification_loop(bot: Bot):
    last_checked = None
    while True:
        now = datetime.now(MINSK)
        if (bell := due_bell(now)) and last_checked != (now.date(), bell[0]):
            try:
                await send_notifications(bot, now, *bell)
            except psycopg.Error as exc:
                logger.warning("Schedule reminder database error (%s)", type(exc).__name__)
            else:
                last_checked = (now.date(), bell[0])
        await asyncio.sleep(5)
