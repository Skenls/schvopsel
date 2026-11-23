import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool
from psycopg_pool import PoolTimeout, TooManyRequests
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders

from .crew import CREW, MAIN_ADMIN_MEMBER, rooms_for
from .crew_store import CrewError, connection_options, crew_details, initialize, lock_crew, update_crew
from .schedule_store import read_schedule
from .telegram_auth import MAX_TELEGRAM_ID, telegram_id_from_init_data
from .timetable import MINSK, schedule_days, schedule_state

MODE = os.getenv("MODE", "prod")
if MODE not in ("prod", "demo"):
    raise RuntimeError("mode must be prod or demo")
SCHEDULE_SOURCE = os.getenv("SCHEDULE_SOURCE") or ("demo" if MODE == "demo" else "telegram")
if SCHEDULE_SOURCE not in ("demo", "telegram"):
    raise RuntimeError("SCHEDULE_SOURCE must be demo or telegram")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").removeprefix("@")
if BOT_USERNAME and not re.fullmatch(r"[A-Za-z0-9_]{5,32}", BOT_USERNAME):
    raise RuntimeError("Invalid TELEGRAM_BOT_USERNAME")
MAIN_ADMIN = int(os.getenv("MAIN_ADMIN_TELEGRAM_ID") or "0")
if MODE == "prod" and (not BOT_TOKEN or not BOT_USERNAME or not 1 <= MAIN_ADMIN <= MAX_TELEGRAM_ID):
    raise RuntimeError("Configure Telegram bot token, username, and main admin ID for prod")

Vessel = Literal["ship", "dinghy"]
Room = Literal["deck", "captain", "gym", "hold", "computers", "bar", "hammam"]


class Move(BaseModel):
    vessel: Vessel
    room: Room


class CrewChange(BaseModel):
    vessel: Vessel | None = None
    telegram_id: Annotated[int, Field(strict=True, ge=1, le=MAX_TELEGRAM_ID)] | None = None
    allowed: bool = False
    is_admin: bool = False
    hidden: bool = False


if MODE == "prod" and os.getenv("PGPASSWORD") in ("local-demo", "replace-with-a-strong-password"):
    raise RuntimeError("Configure a strong PostgreSQL password for prod")

pool = ConnectionPool(
    os.getenv("DATABASE_URL", ""), kwargs=connection_options(MODE),
    min_size=1, max_size=8, timeout=3, max_waiting=32, open=False,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open(wait=True)
    try:
        with pool.connection() as conn:
            initialize(conn, MODE, MAIN_ADMIN)
        yield
    finally:
        pool.close()


app = FastAPI(title="Хамамтон", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secured(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "same-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                if scope["path"].startswith("/api/"):
                    headers["Cache-Control"] = "no-store"
                elif headers.get("content-type", "").startswith("text/html"):
                    headers["Content-Security-Policy"] = (
                        "default-src 'self'; script-src 'self' https://telegram.org; "
                        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; "
                        "base-uri 'none'; form-action 'self'; frame-ancestors 'self' "
                        "https://*.telegram.org https://t.me"
                    )
            await send(message)

        await self.app(scope, receive, secured)


app.add_middleware(SecurityHeaders)


@app.exception_handler(PoolTimeout)
@app.exception_handler(TooManyRequests)
async def busy(request: Request, exc: Exception):
    return JSONResponse({"detail": "Борт занят, попробуйте ещё раз"}, status_code=503,
                        headers={"Retry-After": "2", "Cache-Control": "no-store"})


def identity(request: Request, conn) -> dict:
    if MODE == "demo":
        profile = request.headers.get("x-demo-profile", "0")
        if profile not in {str(i) for i in range(len(CREW))}:
            raise HTTPException(400, "Неизвестный демо-профиль")
        member = conn.execute("""
            SELECT c.*, COALESCE(a.is_admin, FALSE) AS account_admin FROM crew c
            LEFT JOIN telegram_accounts a USING (telegram_id) WHERE c.id = %s
        """, (int(profile),)).fetchone()
        main = member["id"] == MAIN_ADMIN_MEMBER
    else:
        raw = request.headers.get("x-telegram-init-data", "")
        if not raw or len(raw) > 8192:
            raise HTTPException(401, "Откройте мини-приложение из Telegram")
        try:
            telegram_id = telegram_id_from_init_data(raw, BOT_TOKEN)
        except ValueError:
            raise HTTPException(401, "Откройте мини-приложение из Telegram заново") from None
        main = telegram_id == MAIN_ADMIN
        member = conn.execute("""
            SELECT c.*, a.is_admin AS account_admin FROM crew c
            JOIN telegram_accounts a USING (telegram_id) WHERE c.telegram_id = %s AND a.allowed
        """, (telegram_id,)).fetchone()
        if not member:
            raise HTTPException(403, "Вашего Telegram ID нет в белом списке")
    return {
        "id": member["id"], "name": member["name"], "vessel": member["vessel"], "room": member["room"],
        "admin": main or member["account_admin"], "mainAdmin": main,
    }


@app.get("/api/health")
def health():
    with pool.connection() as conn:
        conn.execute("SELECT 1")
    return {"ok": True}


@app.get("/api/config")
def config(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"mode": MODE, "botUrl": f"https://t.me/{BOT_USERNAME}" if BOT_USERNAME else ""}


@app.get("/api/state")
def ship_state(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with pool.connection() as conn:
        viewer = identity(request, conn)
        crew = conn.execute("SELECT id, name, vessel, room FROM crew WHERE NOT hidden ORDER BY id").fetchall()
        profiles = conn.execute("SELECT id, name FROM crew ORDER BY id").fetchall() if MODE == "demo" else []
        now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        documents = read_schedule(conn, schedule_days(now.astimezone(MINSK).date())) if SCHEDULE_SOURCE == "telegram" else None
    return {"me": viewer, "serverNow": int(now.timestamp() * 1000), "crew": crew, "profiles": profiles, "schedule": schedule_state(now, documents)}


@app.put("/api/location")
def move(change: Move, request: Request):
    with pool.connection() as conn:
        lock_crew(conn)
        viewer = identity(request, conn)
        if change.vessel != viewer["vessel"]:
            raise HTTPException(403, "Перейти можно только в отсек своего судна")
        if change.room not in rooms_for(change.vessel):
            raise HTTPException(422, "На этом судне нет такого отсека")
        conn.execute("UPDATE crew SET room = %s WHERE id = %s", (change.room, viewer["id"]))
    return {"ok": True}


@app.get("/api/admin/crew")
def admin_crew(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with pool.connection() as conn:
        viewer = identity(request, conn)
        if not viewer["admin"]:
            raise HTTPException(403, "Требуются права админа")
        return crew_details(conn, viewer["mainAdmin"])


@app.patch("/api/admin/crew/{member_id}")
def update_member(member_id: int, change: CrewChange, request: Request):
    fields = change.model_dump(exclude_unset=True)
    if fields.get("vessel", "") is None:
        raise HTTPException(422, "Укажите судно")
    try:
        with pool.connection() as conn:
            lock_crew(conn)
            viewer = identity(request, conn)
            update_crew(conn, viewer, member_id, fields)
    except CrewError as exc:
        raise HTTPException(exc.status, exc.detail) from None
    except UniqueViolation:
        raise HTTPException(409, "Этот Telegram ID уже привязан к другому участнику") from None
    return {"ok": True}


dist = Path(__file__).resolve().parents[1] / "web" / "dist"
if dist.is_dir():
    app.mount("/", StaticFiles(directory=dist, html=True), name="web")
