from datetime import date, datetime, time, timedelta
from itertools import groupby
from random import Random
from zoneinfo import ZoneInfo

MINSK = ZoneInfo("Europe/Minsk")
WEEKDAY = (
    ("08:00", "08:45"), ("08:55", "09:40"), ("09:50", "10:35"),
    ("10:45", "11:30"), ("11:40", "12:25"), ("12:45", "13:30"),
    ("13:40", "14:25"), ("14:35", "15:20"),
)
SATURDAY = WEEKDAY[:5] + (("12:35", "13:20"), ("13:30", "14:15"), ("14:25", "15:10"))
SUBJECTS = [
    "Математика", "Английский язык", "Информатика", "История",
    "Физика", "Физкультура", "Белорусский язык", "Программирование",
    "Литература", "Компьютерные сети", "Веб-разработка", "Обществоведение",
]


def daily_schedule(day: date) -> list[dict]:
    if day.weekday() == 6:
        return []
    pairs = SATURDAY if day.weekday() == 5 else WEEKDAY
    random = Random(day.isoformat())
    subjects = random.sample(SUBJECTS, len(pairs))
    return [
        {
            "kind": "lesson", "number": index, "start": start, "end": end,
            "subject": subject, "auditorium": str(random.randint(201, 315)),
        }
        for index, ((start, end), subject) in enumerate(zip(pairs, subjects), 1)
    ]


def schedule_days(today: date) -> tuple[date, ...]:
    tomorrow = today + timedelta(days=1)
    if today.weekday() == 5:
        return today, tomorrow, today + timedelta(days=2)
    return today, tomorrow


def channel_lessons(day: date, rows: list[dict]) -> list[dict]:
    pairs = SATURDAY if day.weekday() == 5 else WEEKDAY
    ordered = sorted(rows, key=lambda row: (row["lesson_num"], row["subgroup"]))
    lessons = []
    for number, group in groupby(ordered, key=lambda row: row["lesson_num"]):
        if not 1 <= number <= len(pairs):
            continue
        variants = list(group)
        start, end = pairs[number - 1]
        names = " / ".join(dict.fromkeys(row["subject"] for row in variants))
        lessons.append({
            "kind": "lesson", "number": number, "start": start, "end": end,
            "subject": names, "auditorium": variants[0]["auditorium"], "variants": variants,
        })
    return lessons


def schedule_state(now: datetime, documents: dict[date, list[dict]] | None = None) -> dict:
    local = now.astimezone(MINSK)
    day = local.date()
    next_day = None
    if documents is None:
        lessons = daily_schedule(day)
    else:
        lessons = channel_lessons(day, documents.get(day, []))
        candidates = schedule_days(day)[1:]
        upcoming_day = next((candidate for candidate in candidates if candidate in documents), candidates[-1])
        next_day = {
            "date": upcoming_day.isoformat(), "available": upcoming_day in documents,
            "lessons": channel_lessons(upcoming_day, documents.get(upcoming_day, [])),
        }
    periods = []
    for index, lesson in enumerate(lessons):
        if index and lessons[index - 1]["number"] + 1 == lesson["number"]:
            start, end = lessons[index - 1]["end"], lesson["start"]
            duration = datetime.combine(day, time.fromisoformat(end)) - datetime.combine(
                day, time.fromisoformat(start)
            )
            periods.append({
                "kind": "break", "start": start, "end": end,
                "minutes": int(duration.total_seconds() // 60),
            })
        periods.append(lesson)

    clock = local.strftime("%H:%M")
    current = next((p for p in periods if p["start"] <= clock < p["end"]), None)
    upcoming = next((p for p in periods if p["start"] > clock), None)
    boundary = current["end"] if current else upcoming["start"] if upcoming else None
    deadline = datetime.combine(day, time.fromisoformat(boundary), MINSK) if boundary else None
    return {
        "date": day.isoformat(), "timezone": MINSK.key, "lessons": lessons,
        "source": "demo" if documents is None else "telegram",
        "available": documents is None or day in documents, "nextDay": next_day,
        "current": current, "next": upcoming,
        "deadline": int(deadline.timestamp() * 1000) if deadline else None,
    }
