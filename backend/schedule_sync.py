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
from .schedule_store import ensure_schedule_table, save_schedule, saved_transcriptions
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


async def select_schedules(posts, today, inspect, transcribe):
    targets = schedule_days(today)
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
        if today in found and any(day in found for day in targets[1:]):
            break


class PhotoReader:
    def __init__(self, group, infer):
        self.group = group
        self.infer = infer
        self.pages = {}
        self.lessons = {}

    async def inspect(self, photo):
        page = self.pages.get(photo.id)
        if page is None:
            raw = await photo.download_media(file=bytes)
            if not raw:
                return None, False
            try:
                page = await asyncio.to_thread(locate_page, raw, self.group, self.infer)
            except UnidentifiedImageError:
                LOG.warning("Не удалось открыть фото %s", photo.id)
                return None, False
            if page.day:
                self.pages[photo.id] = page
                if len(self.pages) > 20:
                    oldest = min(self.pages)
                    self.pages.pop(oldest)
                    self.lessons.pop(oldest, None)
        return page.day, page.box is not None

    async def transcribe(self, photo):
        page = self.pages.get(photo.id)
        if page is None:
            raw = await photo.download_media(file=bytes)
            page = await asyncio.to_thread(locate_page, raw, self.group, self.infer)
        if photo.id not in self.lessons:
            self.lessons[photo.id] = await asyncio.to_thread(read_lessons, page, self.group, self.infer)
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


async def run(args):
    from telethon import TelegramClient
    from telethon.errors import RPCError

    session = Path(os.getenv("TELEGRAM_SESSION_PATH", "channel_listener.session"))
    with TemporaryDirectory(prefix="schedule-session-") as directory:
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
            with psycopg.connect(os.getenv("DATABASE_URL", ""), connect_timeout=5) as conn:
                ensure_schedule_table(conn)
            with gemini_client(os.environ["GEMINI_API_KEY"]) as model_client:
                reader = PhotoReader(os.getenv("TARGET_GROUP", "11П"), GeminiReader(model_client, args.model or MODELS))
                while True:
                    try:
                        today = datetime.now(MINSK).date()
                        with psycopg.connect(os.getenv("DATABASE_URL", ""), connect_timeout=5) as conn:
                            reader.lessons.update(saved_transcriptions(conn, schedule_days(today)))
                        saved = False
                        records = select_schedules(photo_posts(client, channel), today, reader.inspect, reader.transcribe)
                        async for day, message_id, lessons in records:
                            with psycopg.connect(os.getenv("DATABASE_URL", ""), connect_timeout=5) as conn:
                                save_schedule(conn, day, message_id, lessons)
                            saved = True
                            LOG.info("Лист %s, сообщение %s, строк: %s", day, message_id, len(lessons))
                        if not saved:
                            LOG.info("Подходящего расписания пока нет")
                    except (OCRUnavailable, RPCError, OSError, psycopg.Error) as exc:
                        LOG.warning("Синхронизация прервана: %s", type(exc).__name__)
                        if args.once:
                            return 1
                    if args.once:
                        return 0
                    await asyncio.sleep(args.interval)
                    reader.infer.unavailable.clear()
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
    except (KeyError, ValueError, RuntimeError, OSError, psycopg.Error) as exc:
        LOG.error("Не удалось запустить синхронизацию: %s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
