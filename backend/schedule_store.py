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


def save_schedule(conn, day, message_id, lessons):
    conn.execute("""
        INSERT INTO public.channel_schedule (day, source_message, lessons)
        VALUES (%s, %s, %s)
        ON CONFLICT (day) DO UPDATE SET
            source_message = excluded.source_message,
            lessons = excluded.lessons,
            updated_at = now()
        WHERE channel_schedule.source_message < excluded.source_message
    """, (day, message_id, Jsonb(lessons)))


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
