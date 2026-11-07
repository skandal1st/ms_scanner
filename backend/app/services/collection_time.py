"""Local accounting date of a shipment, using the server clock and an IANA zone."""
from datetime import datetime
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

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        return validate_timezone(value)


def shipment_moment(started_at: datetime, timezone_name: str) -> str:
    # `moment` is the editable accounting date, not the read-only creation time.
    # Send the chosen location's wall clock in the API's timezone-less format.
    # Keep UTC started_at separately so retries never pick a different date.
    return started_at.astimezone(ZoneInfo(timezone_name)).strftime('%Y-%m-%d %H:%M:00')
