"""Pure helpers that turn a short user argument into a bounded date range."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
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
    """Parse ``""``, ``"<days>"`` or ``"YYYY-MM-DD,YYYY-MM-DD"``.

    Args:
        text: Raw argument. Empty means the last ``DEFAULT_DAYS`` days.
        today: Local business date; defaults to the current Mexico City date.

    Returns:
        An inclusive ``DateRange`` spanning at most ``MAX_DAYS`` days.

    Raises:
        ValueError: If the text is malformed, reversed, in the future, or too long.
    """
    today = today or datetime.now(BUSINESS_TZ).date()
    clean = (text or "").strip()
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
