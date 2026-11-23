from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb


def ensure_schedule_table(conn):
    conn.execute("SELECT pg_advisory_xact_lock(27377)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS public.channel_schedule (
            day DATE PRIMARY KEY,
            source_message BIGINT NOT NULL,
            lessons JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS public.schedule_imports (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            next_day BOOLEAN NOT NULL,
            requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            result TEXT
        )
    """)


def save_schedule(conn, day, message_id, lessons, *, force=False):
    conn.execute("""
        INSERT INTO public.channel_schedule (day, source_message, lessons)
        VALUES (%s, %s, %s)
        ON CONFLICT (day) DO UPDATE SET
            source_message = excluded.source_message,
            lessons = excluded.lessons,
            updated_at = now()
        WHERE channel_schedule.source_message < excluded.source_message
           OR (%s AND channel_schedule.source_message = excluded.source_message)
    """, (day, message_id, Jsonb(lessons), force))


def request_import(conn, next_day):
    conn.execute("SELECT pg_advisory_xact_lock(27379)")
    conn.execute("DELETE FROM public.schedule_imports WHERE requested_at < now() - INTERVAL '1 day'")
    with conn.cursor(row_factory=tuple_row) as cursor:
        row = cursor.execute("""
            SELECT id FROM public.schedule_imports
            WHERE next_day = %s AND result IS NULL AND requested_at > now() - INTERVAL '10 minutes'
            ORDER BY id LIMIT 1
        """, (next_day,)).fetchone()
        if row is None:
            row = cursor.execute("""
                INSERT INTO public.schedule_imports (next_day) VALUES (%s) RETURNING id
            """, (next_day,)).fetchone()
    return row[0]


def next_import(conn):
    return conn.execute("""
        SELECT id, next_day FROM public.schedule_imports
        WHERE result IS NULL AND requested_at > now() - INTERVAL '10 minutes'
        ORDER BY id LIMIT 1
    """).fetchone()


def finish_import(conn, job, result):
    conn.execute("UPDATE public.schedule_imports SET result = %s WHERE id = %s", (result, job))


def import_result(conn, job):
    with conn.cursor(row_factory=tuple_row) as cursor:
        row = cursor.execute("SELECT result FROM public.schedule_imports WHERE id = %s", (job,)).fetchone()
    return row[0] if row else None


def read_schedule(conn, days):
    with conn.cursor(row_factory=tuple_row) as cursor:
        return dict(cursor.execute(
            "SELECT day, lessons FROM public.channel_schedule WHERE day = ANY(%s)",
            (list(days),),
        ).fetchall())


def saved_transcriptions(conn, days):
    return dict(conn.execute(
        "SELECT source_message, lessons FROM public.channel_schedule WHERE day = ANY(%s)",
        (list(days),),
    ).fetchall())
