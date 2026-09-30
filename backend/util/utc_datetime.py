"""UTC-aware DateTime column type.

SQLite has no TIMESTAMPTZ: SQLAlchemy renders DateTime(timezone=True) as a plain
DATETIME, so values come back naive and every reader has to guess. The codebase
compensated by re-attaching UTC at each read site, and one site forgot. This type
settles it once, at the column.

Binds are converted to UTC before storage; reads always come back aware. A naive
bind is read as UTC, which is what every already-persisted value means.
"""

from datetime import timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value
