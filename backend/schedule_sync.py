"""Read timetable photos from the configured Telegram channel into PostgreSQL."""

import argparse
import asyncio
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg
from PIL import UnidentifiedImageError

from .ocr import GeminiReader, MODELS, OCRUnavailable, gemini_client, locate_page, read_lessons
from .schedule_store import ensure_schedule_table, finish_import, next_import, save_schedule, saved_transcriptions
from .timetable import MINSK, schedule_days

LOG = logging.getLogger(__name__)


async def photo_posts(client, channel):
    batch = []
    album = None
    async for message in client.iter_messages(channel, limit=60):
        mime = getattr(message.document, "mime_type", "") or ""
        if not (message.photo or mime.startswith("image/")):
            continue
        key = message.grouped_id or message.id
        if batch and key != album:
            yield batch
            batch = []
        album = key
        batch.append(message)
    if batch:
        yield batch


async def select_schedules(posts, today, inspect, transcribe, *, targets=None):
    manual = targets is not None
    targets = targets if manual else schedule_days(today)
    found = set()
    async for photos in posts:
        pages = [(photo, *await inspect(photo)) for photo in photos]
        if all(day and day < today for _, day, _ in pages):
            LOG.info("Встречены листы за %s, поиск остановлен", max(day for _, day, _ in pages))
            break
        # A matching crop is stronger than a fallback search of a full page.
        for photo, day, has_box in sorted(pages, key=lambda item: not item[2]):
            if day not in targets or day in found:
                continue
            try:
                lessons = await transcribe(photo)
            except ValueError:
                LOG.warning("Не удалось полностью прочитать группу на фото %s", photo.id)
                continue
            if lessons:
                found.add(day)
                yield day, photo.id, lessons
        if manual and found.issuperset(targets):
            break
        if not manual and today in found and found.intersection(targets[1:]):
            break


class PhotoReader:
    def __init__(self, group, infer):
        self.group = group
        self.infer = infer
        self.headers = {}
        self.pages = {}
        self.lessons = {}
        self.rejected = False

    async def inspect(self, photo):
        if photo.id in self.headers:
            return self.headers[photo.id]
        raw = await photo.download_media(file=bytes)
        if not raw:
            return None, False
        try:
            page = await asyncio.to_thread(locate_page, raw, self.group, self.infer)
        except UnidentifiedImageError:
            LOG.warning("Не удалось открыть фото %s", photo.id)
            return None, False
        header = page.day, page.box is not None
        LOG.info("Фото %s: дата листа %s, группа найдена в таблице: %s", photo.id, *header)
        if page.day:
            self.headers[photo.id] = header
            if len(self.headers) > 60:
                self.headers.pop(min(self.headers))
            self.pages[photo.id] = page
            if len(self.pages) > 4:
                self.pages.pop(next(iter(self.pages)))
        return header

    async def transcribe(self, photo):
        if photo.id in self.lessons:
            return self.lessons[photo.id]
        page = self.pages.pop(photo.id, None)
        if page is None:
            raw = await photo.download_media(file=bytes)
            if not raw:
                return []
            try:
                page = await asyncio.to_thread(locate_page, raw, self.group, self.infer)
            except UnidentifiedImageError:
                return []
        try:
            self.lessons[photo.id] = await asyncio.to_thread(read_lessons, page, self.group, self.infer)
        except ValueError:
            self.rejected = True
            raise
        return self.lessons[photo.id]


async def resolve_channel(client):
    from telethon import utils
    from telethon.errors import UsernameInvalidError, UsernameNotOccupiedError

    channel_id = int(os.environ["CHANNEL_USERNAME"])
    try:
        channel = await client.get_entity(os.getenv("CHANNEL_TARGET") or channel_id)
    except (ValueError, UsernameInvalidError, UsernameNotOccupiedError):
        channel = await client.get_entity(channel_id)
    if utils.get_peer_id(channel) != channel_id:
        raise RuntimeError("Имя и ID канала Telegram не совпадают")
    return channel


async def scan(conn, client, channel, reader, *, manual=None):
    today = datetime.now(MINSK).date()
    days = schedule_days(today)
    forced = manual is not None
    targets = (days[-1],) if manual else None
    reader.infer.unavailable.clear()
    reader.rejected = False
    reader.lessons = {} if forced else saved_transcriptions(conn, days)
    if forced:
        reader.headers.clear()
    dates = ", ".join(map(str, targets or days))
    LOG.info("Проверяю фото расписания за %s%s", dates, " по запросу админа" if forced else "")
    found = []
    try:
        records = select_schedules(photo_posts(client, channel), today, reader.inspect, reader.transcribe, targets=targets)
        async for day, message_id, lessons in records:
            save_schedule(conn, day, message_id, lessons, force=forced)
            found.append(str(day))
            LOG.info("Лист %s, сообщение %s, строк: %s", day, message_id, len(lessons))
    finally:
        reader.pages.clear()
    if found:
        return f"Расписание найдено, база обновлена за {', '.join(found)}."
    if reader.rejected:
        return "Фото найдено, но прочитанные уроки не прошли проверку. База не изменена."
    LOG.info("Подходящего расписания пока нет за %s", dates)
    return f"В последних постах не найдено подходящего расписания за {dates}."


async def run(args):
    from telethon import TelegramClient, events
    from telethon.errors import RPCError

    session = Path(os.getenv("TELEGRAM_SESSION_PATH", "channel_listener.session"))
    with (psycopg.connect(os.getenv("DATABASE_URL", ""), connect_timeout=5, autocommit=True) as conn,
          TemporaryDirectory(prefix="schedule-session-") as directory):
        with conn.transaction():
            ensure_schedule_table(conn)
        if not conn.execute("SELECT pg_try_advisory_lock(27378)").fetchone()[0]:
            LOG.error("Другой процесс уже синхронизирует расписание")
            return 1
        copy = Path(directory) / "reader.session"
        shutil.copyfile(session, copy)
        client = TelegramClient(
            str(copy), int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"],
            timeout=15, connection_retries=2,
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise RuntimeError("Сессия Telegram не авторизована")
            channel = await resolve_channel(client)
            published = asyncio.Event()

            @client.on(events.NewMessage(chats=channel))
            async def on_post(event):
                mime = getattr(event.document, "mime_type", "") or ""
                if event.photo or mime.startswith("image/"):
                    LOG.info("Получено фото расписания, сообщение %s", event.id)
                    published.set()

            LOG.info("Синхронизатор запущен. Проверка канала каждые %s с и при новом фото", args.interval)
            loop = asyncio.get_running_loop()
            next_scan = 0.0
            with gemini_client(os.environ["GEMINI_API_KEY"]) as model_client:
                reader = PhotoReader(os.getenv("TARGET_GROUP", "11П"), GeminiReader(model_client, args.model or MODELS))
                while True:
                    job = None if args.once else next_import(conn)
                    if job is None and not published.is_set() and loop.time() < next_scan:
                        try:
                            await asyncio.wait_for(published.wait(), timeout=min(10, next_scan - loop.time()))
                        except TimeoutError:
                            pass
                        continue
                    if job is None and published.is_set():
                        # Give the remaining sheets in a Telegram album time to arrive.
                        await asyncio.sleep(2)
                        published.clear()
                    try:
                        result = await scan(conn, client, channel, reader, manual=job[1] if job else None)
                    except (OCRUnavailable, RPCError, OSError) as exc:
                        LOG.warning("Синхронизация прервана: %s", type(exc).__name__)
                        result = "Импорт не завершился. Подробности в логах schedule-worker."
                        if args.once:
                            return 1
                    if job:
                        finish_import(conn, job[0], result)
                    else:
                        next_scan = loop.time() + args.interval
                    if args.once:
                        return 0
        finally:
            await client.disconnect()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("google_genai").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=600)
    parser.add_argument("--model", action="append")
    args = parser.parse_args()
    if args.interval < 60:
        parser.error("--interval должен быть не меньше 60 секунд")
    try:
        return asyncio.run(run(args))
    except PermissionError:
        LOG.error("Нет доступа к файлу сессии Telegram. Проверьте право чтения у пользователя контейнера.")
        return 1
    except (KeyError, ValueError, RuntimeError, OSError, psycopg.Error) as exc:
        LOG.error("Не удалось запустить синхронизацию: %s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
