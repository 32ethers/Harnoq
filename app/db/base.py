"""Database proxy and shared model bases.

A ``Proxy`` lets models reference the database before the concrete backend is
chosen. ``session.init_db`` binds the real backend (SQLite/PostgreSQL) at app
creation, so the same models work in dev, prod and tests. ``TimestampedModel``
adds ``created_at``/``updated_at`` and bumps ``updated_at`` on every save.
``SoftDeleteModel`` extends it with a nullable ``deleted_at`` for recycle-bin
style deletion.
"""
from peewee import DateTimeField, Model, Proxy

from app.core.time import utcnow

database_proxy = Proxy()


class BaseModel(Model):
    class Meta:
        database = database_proxy


class TimestampedModel(BaseModel):
    created_at = DateTimeField(default=utcnow)
    updated_at = DateTimeField(default=utcnow)

    def save(self, *args, **kwargs):
        self.updated_at = utcnow()
        return super().save(*args, **kwargs)


class SoftDeleteModel(TimestampedModel):
    """Timestamped model that supports soft delete via ``deleted_at``.

    ``deleted_at`` is NULL while the row is live and carries the deletion time
    once the row is moved to the recycle bin. Storing the timestamp (rather than
    a boolean flag) records *when* a row left the pipeline, which serves
    Harnoq's provenance (留痕) goal. Repositories filter ``deleted_at IS NULL``
    for normal reads; recycle-bin reads ask for deleted rows explicitly.
    """

    deleted_at = DateTimeField(null=True)
