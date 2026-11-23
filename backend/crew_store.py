import os

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .crew import CREW, MAIN_ADMIN_MEMBER, initial_vessel
from .schedule_store import ensure_schedule_table


class CrewError(Exception):
    def __init__(self, status, detail):
        self.status = status
        self.detail = detail
        super().__init__(detail)


def connection_options(mode):
    if mode not in ("demo", "prod"):
        raise ValueError("Invalid crew mode")
    return {
        "row_factory": dict_row, "connect_timeout": 3,
        "options": f"-c search_path=hamamton_{mode}",
    }


def connect(mode="prod"):
    return psycopg.connect(os.getenv("DATABASE_URL", ""), **connection_options(mode))


def lock_crew(conn):
    conn.execute("SELECT pg_advisory_xact_lock(27376)")


def initialize(conn, mode, owner):
    lock_crew(conn)
    ensure_schedule_table(conn)
    conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(f"hamamton_{mode}")))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crew (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, vessel TEXT NOT NULL,
            room TEXT NOT NULL DEFAULT 'deck', telegram_id BIGINT UNIQUE,
            allowed BOOLEAN NOT NULL DEFAULT FALSE, is_admin BOOLEAN NOT NULL DEFAULT FALSE,
            hidden BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)
    conn.execute("ALTER TABLE crew ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS telegram_accounts (
            telegram_id BIGINT PRIMARY KEY,
            allowed BOOLEAN NOT NULL DEFAULT FALSE,
            is_admin BOOLEAN NOT NULL DEFAULT FALSE,
            subgroup INTEGER NOT NULL DEFAULT 0 CHECK (subgroup BETWEEN 0 AND 2),
            started BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)
    conn.execute("""
        INSERT INTO telegram_accounts (telegram_id, allowed, is_admin)
        SELECT telegram_id, allowed, is_admin FROM crew WHERE telegram_id IS NOT NULL
        ON CONFLICT (telegram_id) DO NOTHING
    """)
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO crew (id, name, vessel) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
            ((i, name, initial_vessel(name)) for i, name in enumerate(CREW)),
        )
    if mode == "prod":
        other = conn.execute(
            "SELECT id FROM crew WHERE telegram_id = %s AND id <> %s", (owner, MAIN_ADMIN_MEMBER),
        ).fetchone()
        if other:
            raise RuntimeError("MAIN_ADMIN_TELEGRAM_ID must be the Telegram ID of Мышь")
        previous = conn.execute("SELECT telegram_id FROM crew WHERE id = %s", (MAIN_ADMIN_MEMBER,)).fetchone()
        if previous["telegram_id"] not in (None, owner):
            conn.execute(
                "UPDATE telegram_accounts SET allowed = FALSE, is_admin = FALSE, started = FALSE WHERE telegram_id = %s",
                (previous["telegram_id"],),
            )
        conn.execute("UPDATE crew SET telegram_id = %s WHERE id = %s", (owner, MAIN_ADMIN_MEMBER))
        conn.execute("""
            INSERT INTO telegram_accounts (telegram_id, allowed, is_admin) VALUES (%s, TRUE, TRUE)
            ON CONFLICT (telegram_id) DO UPDATE SET allowed = TRUE, is_admin = TRUE
        """, (owner,))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS public.bot_notifications (
                day DATE NOT NULL, slot INTEGER NOT NULL, telegram_id BIGINT NOT NULL,
                PRIMARY KEY (day, slot, telegram_id)
            )
        """)


def bot_actor(conn, telegram_id, owner):
    account = conn.execute(
        "SELECT allowed, is_admin FROM telegram_accounts WHERE telegram_id = %s", (telegram_id,),
    ).fetchone()
    main = telegram_id == owner
    if account and account["allowed"]:
        return {"admin": main or account["is_admin"], "mainAdmin": main}
    return None


def crew_details(conn, main):
    if not main:
        return conn.execute("SELECT id, name, vessel, room, hidden FROM crew ORDER BY id").fetchall()
    return conn.execute("""
        SELECT c.id, c.name, c.vessel, c.room, c.hidden, c.telegram_id,
               COALESCE(a.allowed, FALSE) AS allowed, COALESCE(a.is_admin, FALSE) AS is_admin
        FROM crew c LEFT JOIN telegram_accounts a USING (telegram_id) ORDER BY c.id
    """).fetchall()


def update_crew(conn, viewer, member_id, fields):
    profile_fields = fields.keys() - {"vessel", "hidden"}
    if not viewer or not viewer["admin"] or (profile_fields and not viewer["mainAdmin"]):
        raise CrewError(403, "Недостаточно прав")
    if member_id == MAIN_ADMIN_MEMBER and profile_fields:
        raise CrewError(403, "Права главного админа закреплены за Мышью")
    member = conn.execute(
        "SELECT id, vessel, room, hidden, telegram_id FROM crew WHERE id = %s", (member_id,),
    ).fetchone()
    if not member:
        raise CrewError(404, "Участник не найден")
    updated = member | fields
    if updated["vessel"] != member["vessel"]:
        updated["room"] = "deck"
    if updated["telegram_id"] is not None:
        conn.execute("INSERT INTO telegram_accounts (telegram_id) VALUES (%s) ON CONFLICT DO NOTHING",
                     (updated["telegram_id"],))
        conn.execute("""
            UPDATE telegram_accounts SET allowed = COALESCE(%s, allowed), is_admin = COALESCE(%s, is_admin)
            WHERE telegram_id = %s
        """, (fields.get("allowed"), fields.get("is_admin"), updated["telegram_id"]))
    conn.execute("""
        UPDATE crew SET vessel = %(vessel)s, room = %(room)s, telegram_id = %(telegram_id)s,
                        hidden = %(hidden)s WHERE id = %(id)s
    """, updated)


def update_account(conn, viewer, telegram_id, owner, *, allowed=None, is_admin=None):
    if not viewer or not viewer["mainAdmin"] or telegram_id == owner:
        raise CrewError(403, "Права главного админа защищены")
    conn.execute("""
        INSERT INTO telegram_accounts (telegram_id, allowed, is_admin)
        VALUES (%s, COALESCE(%s, FALSE), COALESCE(%s, FALSE))
        ON CONFLICT (telegram_id) DO UPDATE SET
            allowed = COALESCE(%s, telegram_accounts.allowed),
            is_admin = COALESCE(%s, telegram_accounts.is_admin)
    """, (telegram_id, allowed, is_admin, allowed, is_admin))
