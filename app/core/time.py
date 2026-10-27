"""Time helpers."""
from datetime import datetime, timezone


def utcnow():
    """Naive UTC datetime for storage in a Peewee DateTimeField."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
