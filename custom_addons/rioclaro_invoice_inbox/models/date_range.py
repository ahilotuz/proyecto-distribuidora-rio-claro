"""Pure helpers that turn a short user argument into a bounded date range."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import re
import unicodedata
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("America/Mexico_City")
DEFAULT_DAYS = 30
MAX_DAYS = 366


@dataclass(frozen=True)
class DateRange:
    """Inclusive local-date range with its UTC datetime boundaries."""

    start: date
    end: date

    @property
    def start_utc(self) -> datetime:
        """Naive UTC datetime of the first instant of ``start`` (local time)."""
        return _to_naive_utc(datetime.combine(self.start, time.min))

    @property
    def end_utc_exclusive(self) -> datetime:
        """Naive UTC datetime of the first instant after ``end`` (local time)."""
        return _to_naive_utc(datetime.combine(self.end + timedelta(days=1), time.min))


def _to_naive_utc(local_naive: datetime) -> datetime:
    return local_naive.replace(tzinfo=BUSINESS_TZ).astimezone(timezone.utc).replace(
        tzinfo=None
    )


def parse_date_range(text: str, today: date | None = None) -> DateRange:
    """Parse bounded numeric, explicit, or common natural-language periods.

    Args:
        text: Raw argument. Empty means the last ``DEFAULT_DAYS`` days.
            Also accepts Spanish/English forms for today, yesterday, this/last
            week, this/last month, this year and the last N days.
        today: Local business date; defaults to the current Mexico City date.

    Returns:
        An inclusive ``DateRange`` spanning at most ``MAX_DAYS`` days.

    Raises:
        ValueError: If the text is malformed, reversed, in the future, or too long.
    """
    today = today or datetime.now(BUSINESS_TZ).date()
    clean = (text or "").strip()
    normalized = unicodedata.normalize("NFKD", clean.lower())
    normalized = "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    )
    normalized = re.sub(r"[.!?]+$", "", normalized).strip()

    natural_periods = {
        "hoy": (today, today),
        "today": (today, today),
        "ayer": (today - timedelta(days=1), today - timedelta(days=1)),
        "yesterday": (today - timedelta(days=1), today - timedelta(days=1)),
        "esta semana": (today - timedelta(days=today.weekday()), today),
        "semana actual": (today - timedelta(days=today.weekday()), today),
        "this week": (today - timedelta(days=today.weekday()), today),
        "semana pasada": (
            today - timedelta(days=today.weekday() + 7),
            today - timedelta(days=today.weekday() + 1),
        ),
        "last week": (
            today - timedelta(days=today.weekday() + 7),
            today - timedelta(days=today.weekday() + 1),
        ),
        "este mes": (today.replace(day=1), today),
        "mes actual": (today.replace(day=1), today),
        "this month": (today.replace(day=1), today),
        "mes pasado": (
            (today.replace(day=1) - timedelta(days=1)).replace(day=1),
            today.replace(day=1) - timedelta(days=1),
        ),
        "mes anterior": (
            (today.replace(day=1) - timedelta(days=1)).replace(day=1),
            today.replace(day=1) - timedelta(days=1),
        ),
        "last month": (
            (today.replace(day=1) - timedelta(days=1)).replace(day=1),
            today.replace(day=1) - timedelta(days=1),
        ),
        "este ano": (today.replace(month=1, day=1), today),
        "ano actual": (today.replace(month=1, day=1), today),
        "this year": (today.replace(month=1, day=1), today),
    }
    if normalized in natural_periods:
        start, end = natural_periods[normalized]
        return DateRange(start, end)

    relative_days = re.fullmatch(
        r"(?:ultimos?|last)\s+([0-9]{1,3})\s+(?:dias?|days?)", normalized
    )
    if relative_days:
        days = int(relative_days.group(1))
        if not 1 <= days <= MAX_DAYS:
            raise ValueError("days out of range")
        return DateRange(today - timedelta(days=days - 1), today)

    if not clean:
        days = DEFAULT_DAYS
    elif clean.isascii() and clean.isdigit():
        days = int(clean)
        if not 1 <= days <= MAX_DAYS:
            raise ValueError("days out of range")
    else:
        parts = [part.strip() for part in clean.split(",")]
        if len(parts) != 2:
            raise ValueError("expected two dates")
        start, end = (date.fromisoformat(part) for part in parts)
        if len(parts[0]) != 10 or len(parts[1]) != 10:
            raise ValueError("use YYYY-MM-DD")
        if start > end or end > today:
            raise ValueError("invalid order or future date")
        if (end - start).days + 1 > MAX_DAYS:
            raise ValueError("range too long")
        return DateRange(start, end)
    return DateRange(today - timedelta(days=days - 1), today)
