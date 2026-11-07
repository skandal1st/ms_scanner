"""Local accounting date of a shipment, using the server clock and an IANA zone."""
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator


def validate_timezone(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError('Неизвестный часовой пояс. Выберите часовой пояс юрлица в настройках.')
    return value


class CollectionStartRequest(BaseModel):
    timezone: str | None = Field(default=None, max_length=100)
    # JS getTimezoneOffset has the opposite sign; the browser sends UTC eastward.
    utc_offset_minutes: int | None = Field(default=None, ge=-720, le=840, strict=True)

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        return validate_timezone(value)


def resolve_collection_timezone(timezone_name: str | None, utc_offset_minutes: int | None = None) -> tzinfo:
    if timezone_name:
        return ZoneInfo(validate_timezone(timezone_name))
    if (isinstance(utc_offset_minutes, int) and not isinstance(utc_offset_minutes, bool)
            and -720 <= utc_offset_minutes <= 840):
        return timezone(timedelta(minutes=utc_offset_minutes))
    raise ValueError('Не удалось получить часовой пояс устройства. Обновите страницу ТСД. '
                     'Если ошибка повторяется, выберите часовой пояс юрлица в настройках.')


def shipment_moment(started_at: datetime, timezone_name: str | tzinfo) -> str:
    # `moment` is the editable accounting date, not the read-only creation time.
    # Send the chosen location's wall clock in the API's timezone-less format.
    # Keep UTC started_at separately so retries never pick a different date.
    zone = ZoneInfo(timezone_name) if isinstance(timezone_name, str) else timezone_name
    return started_at.astimezone(zone).strftime('%Y-%m-%d %H:%M:00')
