"""Read the printed date and physical rows of a Telegram timetable photo."""

import argparse
import io
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from heapq import nlargest
from itertools import groupby
from pathlib import Path
from time import sleep
from typing import Literal

from PIL import Image, ImageFilter, ImageOps
from pydantic import BaseModel, ValidationError

LOG = logging.getLogger(__name__)
MODELS = ("gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
WEEKDAYS = ("понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресень")
TICKS = {"✓", "√", "✔", "V", "v"}


class Header(BaseModel):
    printed_date: str | None
    weekday: str | None


class Location(Header):
    group_label: str | None
    box_2d: list[int] | None


class Row(BaseModel):
    number: int | None
    kind: Literal["lesson", "tick", "unclear"]
    subject: str | None
    room: str | None
    teacher: str | None


class Transcription(BaseModel):
    group_label: str | None
    complete: bool
    rows: list[Row]


class RoomRow(BaseModel):
    number: int
    subgroup: Literal[0, 1, 2]
    room: str | None


class RoomGrid(BaseModel):
    group_label: str | None
    rooms: list[RoomRow]


@dataclass
class Page:
    image: Image.Image
    day: date | None
    box: list[int] | None


def same_group(raw: str | None, group: str) -> bool:
    return bool(raw) and "".join(raw.split()).casefold() == group.casefold()


def printed_day(raw: str | None, weekday: str | None = None) -> date | None:
    value = (raw or "").strip().lower()
    match = re.fullmatch(r"(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})\s*(?:г\.?)?", value)
    if not match:
        return None
    day, month, year = map(int, match.groups())
    try:
        result = date(year + 2000 if year < 100 else year, month, day)
    except ValueError:
        return None
    if weekday and not weekday.strip().casefold().startswith(WEEKDAYS[result.weekday()]):
        return None
    return result


def jpeg(image: Image.Image, upscale: float = 1) -> bytes:
    if upscale != 1:
        size = tuple(round(n * upscale) for n in image.size)
        image = image.resize(size, Image.Resampling.LANCZOS)
    result = io.BytesIO()
    image.save(result, "JPEG", quality=95)
    return result.getvalue()


def locate_page(image_bytes: bytes, group: str, infer) -> Page:
    with Image.open(io.BytesIO(image_bytes)) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
    w, h = image.size
    header = image.crop((0, 0, w, round(h * .36)))
    prompt = f"""
Первый снимок — целый лист расписания. Второй — увеличенный верх первого.
Прочитай точную дату ДД.ММ.ГГ из печатного заголовка «РАСПИСАНИЕ НА» над основной
таблицей и видимый русский день недели. На фото бывает кусок соседнего листа
за синей рамкой. Игнорируй его, подписи, сноски и рукописные даты. Если день
нельзя уверенно прочитать, верни null. Не подставляй дату из контекста.

Просмотри каждый столбец «№ гр.» во всех секциях основной страницы, включая низ
правой таблицы. Найди ровно «{group}», не 10П, 13П, 14П или 1006. На другом листе
того же дня этой группы может не быть. group_label — надпись, которую реально
видишь в ячейке нужной группы, или null. Если нашёл, box_2d — четыре целых
[ymin,xmin,ymax,xmax] в шкале 0–1000 относительно ПЕРВОГО снимка. Рамка охватывает
только горизонтальную полосу строки группы от её верхней до нижней границы,
слева ячейку «№ гр.», справа всю колонку «Преподаватель». Не давай рамку лишь
маленькой ячейки с номером группы или всей большой таблицы.
"""
    result = infer(Location, prompt, jpeg(image), jpeg(header, 1.75))
    box = result.box_2d if same_group(result.group_label, group) else None
    day = printed_day(result.printed_date, result.weekday)
    if day is None:
        checked = infer(
            Header,
            "Прочитай только напечатанную дату ДД.ММ.ГГ из заголовка «РАСПИСАНИЕ НА» "
            "над основной таблицей. Если видны повторяющиеся заголовки, сравни их даты. "
            "Передай русский день недели, если он читается, иначе null. "
            "Не используй рукописные даты или догадки.",
            jpeg(ImageOps.autocontrast(header.convert("L")), 2.1),
        )
        day = printed_day(checked.printed_date, checked.weekday)
    return Page(image, day, box)


def straight_group_crops(image: Image.Image, top: int, bottom: int, *, leveled=False) -> list[Image.Image]:
    """Tighten a near-horizontal group crop to the printed table borders."""
    w, h = image.size
    if w < 40 or h < 30:
        return []
    pixels = image.convert("L").load()
    columns = [round(w * x / 100) for x in range(7, 86, 2)]
    group_columns = [round(w * x / 100) for x in range(3, 15, 2)]
    lesson_columns = [round(w * x / 100) for x in range(16, 72, 2)]

    def rule_strength(y, sample):
        return sum(
            pixels[x, y - 5] + pixels[x, y + 5] - 2 * min(pixels[x, y + dy] for dy in range(-2, 3)) > 24
            for x in sample
        ) / len(sample)

    scores = []
    for y in range(5, h - 5):
        if leveled:
            score = rule_strength(y, group_columns) * rule_strength(y, lesson_columns)
        else:
            score = sum(pixels[x, y - 4] + pixels[x, y + 4] - 2 * pixels[x, y] > 44 for x in columns) / len(columns)
        scores.append((score, y))
    peaks = []
    threshold = .42 if leveled else .51
    for strong, rows in groupby(scores, lambda row: row[0] > threshold):
        if not strong:
            continue
        score, y = max(rows)
        if peaks and y - peaks[-1][0] <= 12:
            if score > peaks[-1][1]:
                peaks[-1] = (y, score)
        else:
            peaks.append((y, score))
    pairs = [(a, b) for (a, _), (b, _) in zip(peaks, peaks[1:]) if 24 <= b - a <= (bottom - top) * 1.6]
    def distance(pair):
        return (2 if leveled else 1) * abs(pair[0] - top) + abs(pair[1] - bottom)

    return [
        image.crop((0, max(0, first - 3), w, min(h, last + 4)))
        for first, last in sorted(pairs, key=distance)[:2]
        if distance((first, last)) <= max(90, bottom - top)
    ]


def level_group_crop(image: Image.Image) -> Image.Image:
    width, height = image.size
    if width < 100 or height < 55:
        return image
    pixels = image.convert("L").load()
    columns = range(round(width * .10), round(width * .85), max(2, width // 48))
    margin = round(width * .06) + 4
    if height <= 2 * margin:
        return image

    def line_score(slope):
        samples = [(x, round((x - width / 2) * slope)) for x in columns]
        rows = (
            sum(max(0, min(80, pixels[x, y + shift - 3] + pixels[x, y + shift + 3]
                           - 2 * pixels[x, y + shift])) for x, shift in samples)
            for y in range(margin, height - margin)
        )
        return sum(nlargest(6, rows))

    flat = line_score(0)
    score, slope = max((line_score(step * .015), step * .015) for step in range(-8, 9) if step)
    if score <= flat * 1.08:
        return image
    return image.transform(
        image.size, Image.Transform.AFFINE, (1, 0, 0, slope, 1, -slope * width / 2),
        Image.Resampling.BICUBIC, fillcolor="white",
    )


def group_crops(page: Page) -> list[Image.Image]:
    box = page.box
    if not box or len(box) != 4 or any(not 0 <= value <= 1000 for value in box):
        return []
    y0, x0, y1, x1 = box
    if y1 <= y0 or x1 - x0 < 150:
        return []
    w, h = page.image.size
    pad = min(85, max(52, (y1 - y0) // 3))
    start_y = max(0, round((y0 - pad) * h / 1000))
    crop = page.image.crop((
        max(0, round((x0 - 20) * w / 1000)), start_y,
        min(w, round((x1 + 65) * w / 1000)), min(h, round((y1 + pad) * h / 1000)),
    ))
    aligned = level_group_crop(crop)
    tight = straight_group_crops(
        aligned, round(y0 * h / 1000) - start_y, round(y1 * h / 1000) - start_y, leveled=aligned is not crop,
    )
    return [part for part in tight if part.height < crop.height * .85] + [aligned]


def room_code(room: str | None) -> str:
    value = re.sub(r"\s+", "", (room or "").casefold())
    if value in ("", "-", "—", "нет", "неткабинета"):
        return ""
    if value in ("ч/з", "чз", "ч/3", "9/3", "9/з", "читальныйзал"):
        return "ч/з"
    if re.fullmatch(r"[0-9]{1,4}", value):
        return value
    raise ValueError("Unrecognized room code")


def lessons_from_rows(rows: list[Row]) -> list[dict]:
    slots: dict[int, list[Row]] = {}
    for row in rows:
        number = row.number
        if number is not None:
            if not 1 <= number <= 13 or (slots and number <= next(reversed(slots))):
                raise ValueError("Lesson numbers are not in printed order")
            slots[number] = [row]
        elif slots and len(slots[next(reversed(slots))]) == 1:
            slots[next(reversed(slots))].append(row)
        else:
            raise ValueError("Continuation row has no matching printed lesson")
    lessons = []
    for number, lines in slots.items():
        for position, row in enumerate(lines, start=1):
            subject = " ".join((row.subject or "").split())
            if row.kind == "tick":
                if subject and subject not in TICKS:
                    raise ValueError("Tick row also contains a discipline")
                continue
            if row.kind != "lesson" or not subject or subject in TICKS:
                raise ValueError("Unreadable lesson row")
            lessons.append({
                "lesson_num": number, "subgroup": position if len(lines) == 2 else 0,
                "subject": subject, "auditorium": room_code(row.room),
                "teacher": " ".join((row.teacher or "").split()),
            })
    return lessons


def verify_room_grid(lessons: list[dict], group: str, image: bytes, infer) -> list[dict]:
    if not lessons:
        return lessons
    prompt = f"""
Независимо сверь ТОЛЬКО сетку номеров «№ ур» и «Ауд» в группе «{group}».
Первое изображение — исходный фрагмент. Во втором слева увеличены колонки
«№ гр.» и «№ ур», справа увеличена область с «Ауд». Строки обеих частей второго
изображения лежат на одинаковой высоте. Читай каждую видимую цифру номера
отдельно, даже если следующие друг за другом уроки имеют одинаковый кабинет.
Сначала найди точную надпись группы. Для каждого занятия укажи напечатанный
номер урока, subgroup (0 — единственная строка, 1 — верхняя, 2 — нижняя)
и room из колонки «Ауд». Считывай цифры строго по горизонтали относительно
верхней или нижней строки. Игнорируй текст предметов и преподавателей.
Если следующий номер пуст, но есть строка занятия или галочка, это две
подгруппы предыдущего номера. Урок напротив галочки отсутствует: саму
галочку в результат не включай, но сохрани положение другой подгруппы.
У пустого кабинета room=null, у печатного «ч/з» верни «ч/з». «ч/з» может выглядеть
как «9/3». Не копируй цифры из соседней строки и не придумывай пропущенный
номер. Между разными номерами уроков нет группировки, даже если кабинет одинаков.
Если не видишь надпись группы или сетку целиком, верни group_label=null и rooms=[].
"""
    with Image.open(io.BytesIO(image)) as original:
        w, h = original.size
        numbers_width = round(w * .28)
        rooms = original.crop((round(w * .32), 0, round(w * .76), h)).convert("L")
        columns = Image.new("L", (numbers_width + rooms.width + 8, h), 255)
        columns.paste(original.crop((0, 0, numbers_width, h)).convert("L"), (0, 0))
        columns.paste(rooms, (numbers_width + 8, 0))
        zoom = ImageOps.autocontrast(columns).filter(ImageFilter.UnsharpMask(radius=1, percent=85))
        numbers_view = jpeg(zoom, 1.8)
    result = infer(RoomGrid, prompt, image, numbers_view)
    rooms = {(row.number, row.subgroup): room_code(row.room) for row in result.rooms}
    expected = {(row["lesson_num"], row["subgroup"]) for row in lessons}
    if not same_group(result.group_label, group) or rooms.keys() != expected or len(rooms) != len(result.rooms):
        raise ValueError("Printed numbers could not be verified")
    return [row | {"auditorium": rooms[row["lesson_num"], row["subgroup"]]} for row in lessons]


def read_lessons(page: Page, group: str, infer) -> list[dict]:
    prompt = f"""
Перепиши физические строки группы «{group}» сверху вниз из таблицы расписания.
Указывай group_label только если реально видна точная буква П и обе единицы в
колонке «№ гр.». Остановись на следующей сплошной границе группы, не включай
соседние 123д, 1006, 10П, 13П, 14П и прочие. complete=true только если видны обе
границы этой группы и её строки. Если группы нет, верни null, false и [].

Для каждой печатной строки:
- number — цифра из узкой колонки «№ ур» на этой строке. Если там пусто,
  верни null. Следуй горизонтали. Не копируй предыдущую цифру; не дополняй
  последовательность (уроки могут начинаться с 2, между ними бывают пропуски).
- kind=lesson и subject — видимая дисциплина. Даже две одинаковые строки
  предмета подряд нужно переписать отдельно. Нижняя с пустым номером — вторая
  подгруппа. Одна строка без нижней относится ко всей группе.
- Галочка √/✓ в колонке предмета означает отсутствие урока, kind=tick,
  subject=null. Перепиши её собственной строкой и с номером, только если цифра
  стоит напротив неё. Если в верхней строке галочка с номером, а ниже предмет
  без цифры, это занятие второй подгруппы. СМГ/СМГ6 — предмет, не галочка.
- room — номер из колонки «Ауд» как строка; «ч/з» — читальный зал, пусто — null.
  Напечатанное «ч/з» на фото похоже на «9/3». В этой колонке дробей нет.
  Переписывай каждую строку отдельно. Проверь, что кабинет стоит напротив этой
  строки. У одинакового предмета подгрупп часто разные кабинеты.
  Не дублируй нижний кабинет в верхней строке.
- teacher — видимая фамилия с инициалами, или null, если имя не прочитать.
- Если дисциплина не читается, kind=unclear. Если не уверен только в номере
  кабинета, запиши дисциплину как обычно, а room оставь null.
"""
    crops = group_crops(page)
    views = []
    for crop in crops:
        views.append((jpeg(crop, 2.3),))
    if crops:
        crop = crops[-1]
        clearer = ImageOps.autocontrast(crop.convert("L")).filter(ImageFilter.UnsharpMask(radius=1, percent=85))
        views.append((jpeg(clearer, 2.5),))
    w, h = page.image.size
    right = page.image.crop((round(w * .39), 0, w, h))
    views.append((jpeg(page.image), jpeg(right, 1.45)))
    for images in views:
        parsed = infer(Transcription, prompt, *images)
        if not (same_group(parsed.group_label, group) and parsed.complete):
            continue
        try:
            rows = [row.model_copy(update={"room": None}) for row in parsed.rows]
            return verify_room_grid(lessons_from_rows(rows), group, images[0], infer)
        except ValueError:
            LOG.warning("Строки группы %s не прошли проверку", group)
    if page.box is not None:
        raise ValueError("Не удалось полностью прочитать строки найденной группы")
    return []


def parse_image(image_bytes: bytes, target_group: str, infer) -> dict:
    page = locate_page(image_bytes, target_group, infer)
    if not page.day:
        return {"date": None, "group_found": False, "lessons": []}
    lessons = read_lessons(page, target_group, infer)
    return {"date": page.day.isoformat(), "group_found": bool(lessons), "lessons": lessons}


class OCRUnavailable(RuntimeError):
    def __init__(self, message, *, blocked=False):
        super().__init__(message)
        self.blocked = blocked


class GeminiReader:
    def __init__(self, client, models):
        self.client = client
        self.models = tuple(models)
        self.unavailable = set()

    def __call__(self, schema, prompt, *images):
        from google.genai import errors, types
        from httpx2 import TransportError

        parts = [types.Part.from_bytes(data=image, mime_type="image/jpeg") for image in images]
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_json_schema=schema.model_json_schema(),
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
        )
        candidates = [model for model in self.models if model not in self.unavailable]
        for delay in (0, 2, 6):
            if not candidates:
                break
            if delay:
                LOG.info("Повторяю этап Gemini %s через %s с", schema.__name__, delay)
                sleep(delay)
            transient = []
            for model in candidates:
                try:
                    response = self.client.models.generate_content(model=model, contents=[*parts, prompt], config=config)
                    result = schema.model_validate_json(response.text or "")
                    LOG.info("OCR %s: %s", schema.__name__, model)
                    return result
                except errors.APIError as exc:
                    if exc.code in (401, 403, 404, 429):
                        self.unavailable.add(model)
                    elif exc.code in (408, 500, 502, 503, 504):
                        transient.append(model)
                    LOG.warning("Gemini %s (%s): HTTP %s", model, schema.__name__, exc.code)
                except TransportError:
                    transient.append(model)
                    LOG.warning("Gemini %s (%s): обрыв связи", model, schema.__name__)
                except ValidationError:
                    LOG.warning("Gemini %s (%s): некорректный ответ", model, schema.__name__)
            candidates = transient
        raise OCRUnavailable(
            f"Gemini не завершил этап {schema.__name__}",
            blocked=all(model in self.unavailable for model in self.models),
        )


def gemini_client(api_key: str):
    from google import genai
    from google.genai import types

    return genai.Client(api_key=api_key, http_options=types.HttpOptions(
        timeout=20000, retry_options=types.HttpRetryOptions(attempts=1),
    ))


def parse_image_with_gemini(image_bytes: bytes, target_group: str, api_key: str, models=MODELS) -> dict:
    with gemini_client(api_key) as client:
        return parse_image(image_bytes, target_group, GeminiReader(client, models))


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cli = argparse.ArgumentParser(description="Извлечь расписание группы из фотографии")
    cli.add_argument("image", type=Path)
    cli.add_argument("--group", default="11П")
    cli.add_argument("--model", action="append", help="Модели Gemini в порядке перебора")
    args = cli.parse_args()
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        cli.error("Укажите GEMINI_API_KEY в окружении")
    result = parse_image_with_gemini(args.image.read_bytes(), args.group, key, args.model or MODELS)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
