"""Datetime helpers shared across mail sync, AI normalization, and scheduling."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

_WEEKDAY_LOOKUP = {
    "월요": 0,
    "월요일": 0,
    "mon": 0,
    "monday": 0,
    "화요": 1,
    "화요일": 1,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "수요": 2,
    "수요일": 2,
    "wed": 2,
    "wednesday": 2,
    "목요": 3,
    "목요일": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "금요": 4,
    "금요일": 4,
    "fri": 4,
    "friday": 4,
    "토요": 5,
    "토요일": 5,
    "sat": 5,
    "saturday": 5,
    "일요": 6,
    "일요일": 6,
    "sun": 6,
    "sunday": 6,
}
_WEEKDAY_PATTERN = re.compile(
    r"(?P<prefix>이번|오는|다음|this|next)?\s*(?P<weekday>"
    r"월요일|화요일|수요일|목요일|금요일|토요일|일요일|"
    r"월요|화요|수요|목요|금요|토요|일요|"
    r"monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun"
    r")",
    re.IGNORECASE,
)
_DATE_YMD_PATTERN = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*(?:[./-]|년)\s*(?P<month>\d{1,2})\s*(?:[./-]|월)\s*(?P<day>\d{1,2})\s*일?",
    re.IGNORECASE,
)
_DATE_MD_PATTERN = re.compile(
    r"(?<!\d)(?P<month>\d{1,2})\s*(?:[./-]|월)\s*(?P<day>\d{1,2})\s*일?",
    re.IGNORECASE,
)
_TIME_COLON_PATTERN = re.compile(
    r"(?<!\d)(?P<hour>\d{1,2}):(?P<minute>\d{2})(?:\s*(?P<ampm>am|pm))?(?!\d)",
    re.IGNORECASE,
)
_TIME_AMPM_PATTERN = re.compile(
    r"(?<!\d)(?P<hour>\d{1,2})\s*(?P<ampm>am|pm)\b",
    re.IGNORECASE,
)
_TIME_KO_PATTERN = re.compile(
    r"(?:(?P<ampm>오전|오후)\s*)?(?P<hour>\d{1,2})\s*시(?:\s*(?P<minute>\d{1,2})\s*분?)?(?P<half>\s*반)?",
    re.IGNORECASE,
)
_SAME_DAY_PATTERN = re.compile(r"(오늘|금일|today|eod|end of day)", re.IGNORECASE)
_TOMORROW_PATTERN = re.compile(r"(내일|tomorrow)", re.IGNORECASE)
_DAY_AFTER_TOMORROW_PATTERN = re.compile(r"(모레|day after tomorrow)", re.IGNORECASE)
_DUE_SIGNAL_PATTERN = re.compile(
    r"(까지|전까지|마감|기한|deadline|due|reply by|submit by|send by|before\b|by\b)",
    re.IGNORECASE,
)


def to_local_naive(value: datetime | None) -> datetime | None:
    """Convert aware datetimes to local naive datetimes for SQLite storage."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(microsecond=0)
    return value.astimezone().replace(tzinfo=None, microsecond=0)


def parse_datetime_text(value: str | None) -> datetime | None:
    """Parse common datetime string formats into a local naive datetime."""

    if not value or not value.strip():
        return None

    text = value.strip()
    normalized = text.replace("T", " ").replace("/", "-").replace(".", "-")
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        return to_local_naive(datetime.fromisoformat(normalized))
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(normalized, fmt)
            return parsed
        except ValueError:
            continue
    return None


def parse_time_text(value: str | None) -> time | None:
    """Parse time text in HH:MM or HH:MM:SS formats."""

    if not value or not value.strip():
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).time()
        except ValueError:
            continue
    return None


def normalize_due_date_text(value: str | None, reference_datetime: datetime | str | None = None) -> str | None:
    """Return a SQLite-friendly due_date string or None."""

    parsed = parse_datetime_text(value)
    include_time = parsed is not None and parsed.time() != time(0, 0, 0)
    if parsed is None:
        parsed, include_time = _parse_relative_due_date_text(value, reference_datetime)
    if parsed is None:
        return None

    if not include_time:
        return parsed.date().isoformat()
    return parsed.isoformat(sep=" ")


def extract_due_date_hint(
    text: str | None,
    reference_datetime: datetime | str | None = None,
    *,
    require_signal: bool = False,
) -> tuple[str | None, str | None]:
    """Scan freeform text and return one deadline-like snippet with a normalized due date."""

    collapsed = " ".join(str(text or "").split())
    if not collapsed:
        return None, None

    reference = _coerce_reference_datetime(reference_datetime)
    candidates: list[tuple[int, float, str, str]] = []
    for chunk in _split_due_chunks(collapsed):
        if require_signal and not _looks_like_due_context(chunk):
            continue
        normalized = normalize_due_date_text(chunk, reference)
        if not normalized:
            continue
        parsed = parse_datetime_text(normalized)
        is_future = 1
        distance = float("inf")
        if parsed is not None and reference is not None:
            is_future = 0 if parsed >= reference else 1
            distance = abs((parsed - reference).total_seconds())
        candidates.append((is_future, distance, chunk[:120], normalized))

    if not candidates:
        return None, None

    candidates.sort(key=lambda item: (item[0], item[1], len(item[2])))
    _, _, raw_text, normalized = candidates[0]
    return raw_text, normalized


def combine_date_and_time(base: datetime, clock: time) -> datetime:
    """Replace the time portion of a datetime."""

    return datetime.combine(base.date(), clock).replace(microsecond=0)


def today_local() -> date:
    """Return the current local date."""

    return datetime.now().date()


def _coerce_reference_datetime(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        return to_local_naive(value)
    if isinstance(value, str):
        return parse_datetime_text(value)
    return None


def _parse_relative_due_date_text(
    value: str | None,
    reference_datetime: datetime | str | None = None,
) -> tuple[datetime | None, bool]:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None, False

    reference = _coerce_reference_datetime(reference_datetime)
    if reference is None:
        return None, False

    resolved_date = _extract_explicit_date(text, reference.date())
    resolved_time, include_time = _extract_time_hint(text)

    if resolved_date is None:
        resolved_date = _extract_relative_date(text, reference.date())

    if resolved_date is None and include_time and _SAME_DAY_PATTERN.search(text):
        resolved_date = reference.date()

    if resolved_date is None:
        return None, False

    return datetime.combine(resolved_date, resolved_time or time(0, 0, 0)).replace(microsecond=0), include_time


def _extract_explicit_date(text: str, reference_date: date) -> date | None:
    match = _DATE_YMD_PATTERN.search(text)
    if match:
        return _safe_date(int(match.group("year")), int(match.group("month")), int(match.group("day")))

    match = _DATE_MD_PATTERN.search(text)
    if match:
        month = int(match.group("month"))
        day = int(match.group("day"))
        candidate = _safe_date(reference_date.year, month, day)
        if candidate is None:
            return None
        if candidate < reference_date - timedelta(days=7):
            future_candidate = _safe_date(reference_date.year + 1, month, day)
            if future_candidate is not None:
                return future_candidate
        return candidate
    return None


def _extract_relative_date(text: str, reference_date: date) -> date | None:
    lowered = text.lower()

    if _DAY_AFTER_TOMORROW_PATTERN.search(lowered):
        return reference_date + timedelta(days=2)
    if _TOMORROW_PATTERN.search(lowered):
        return reference_date + timedelta(days=1)
    if _SAME_DAY_PATTERN.search(lowered):
        return reference_date

    weekday_match = _WEEKDAY_PATTERN.search(lowered)
    if not weekday_match:
        return None

    weekday_key = weekday_match.group("weekday").lower()
    target_weekday = _WEEKDAY_LOOKUP.get(weekday_key)
    if target_weekday is None:
        return None

    days_until = (target_weekday - reference_date.weekday()) % 7
    prefix = (weekday_match.group("prefix") or "").lower()
    if prefix in {"next", "다음"}:
        days_until = days_until + 7 if days_until > 0 else 7
    elif days_until == 0:
        days_until = 0
    elif days_until < 0:
        days_until += 7
    return reference_date + timedelta(days=days_until)


def _extract_time_hint(text: str) -> tuple[time | None, bool]:
    lowered = text.lower()
    if "정오" in text or "noon" in lowered:
        return time(12, 0), True
    if "자정" in text or "midnight" in lowered:
        return time(0, 0), True

    colon_match = _TIME_COLON_PATTERN.search(text)
    if colon_match:
        hour = int(colon_match.group("hour"))
        minute = int(colon_match.group("minute"))
        ampm = (colon_match.group("ampm") or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        return _safe_time(hour, minute), True

    ampm_match = _TIME_AMPM_PATTERN.search(text)
    if ampm_match:
        hour = int(ampm_match.group("hour"))
        ampm = (ampm_match.group("ampm") or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        return _safe_time(hour, 0), True

    ko_match = _TIME_KO_PATTERN.search(text)
    if not ko_match:
        return None, False

    hour = int(ko_match.group("hour"))
    minute_text = ko_match.group("minute")
    minute = int(minute_text) if minute_text is not None else 0
    if ko_match.group("half") and minute_text is None:
        minute = 30
    ampm = (ko_match.group("ampm") or "").strip()
    if ampm == "오후" and hour < 12:
        hour += 12
    if ampm == "오전" and hour == 12:
        hour = 0
    return _safe_time(hour, minute), True


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _safe_time(hour: int, minute: int) -> time | None:
    try:
        return time(hour, minute)
    except ValueError:
        return None


def _looks_like_due_context(text: str) -> bool:
    lowered = text.lower()
    if _DUE_SIGNAL_PATTERN.search(lowered):
        return True
    return bool(_SAME_DAY_PATTERN.search(lowered) and _extract_time_hint(text)[1])


def _split_due_chunks(text: str) -> list[str]:
    chunks = re.split(r"[\r\n]+|(?<=[.!?])\s+|[;•]+", text)
    return [chunk.strip(" \t-") for chunk in chunks if chunk and chunk.strip()]
