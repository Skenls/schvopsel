import asyncio
import logging
import os

from aiogram import Bot, Dispatcher

from .bot_admin import router as admin_router
from .bot_schedule import notification_loop, router as schedule_router
from .crew_store import connect, initialize
from .telegram_auth import MAX_TELEGRAM_ID


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    owner = int(os.getenv("MAIN_ADMIN_TELEGRAM_ID") or "0")
    if not token or not 1 <= owner <= MAX_TELEGRAM_ID:
        raise RuntimeError("Configure TELEGRAM_BOT_TOKEN and MAIN_ADMIN_TELEGRAM_ID")
    if os.getenv("PGPASSWORD") in ("local-demo", "replace-with-a-strong-password"):
        raise RuntimeError("Configure a strong PostgreSQL password for the Telegram bot")
    with connect("prod") as conn:
        initialize(conn, "prod", owner)
    dispatcher = Dispatcher()
    dispatcher.include_routers(admin_router, schedule_router)
    async with Bot(token=token) as bot:
        notifications = asyncio.create_task(notification_loop(bot))
        try:
            await dispatcher.start_polling(
                bot, owner_id=owner, allowed_updates=dispatcher.resolve_used_update_types(),
                tasks_concurrency_limit=12, close_bot_session=False,
            )
        finally:
            notifications.cancel()
            await asyncio.gather(notifications, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
