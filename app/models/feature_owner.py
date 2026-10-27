from peewee import CharField, ForeignKeyField

from app.db.base import TimestampedModel
from app.models.feature import Feature, FeatureVersion
from app.models.user import User


class FeatureOwner(TimestampedModel):
    feature = ForeignKeyField(Feature, unique=True, backref="ownership", on_delete="CASCADE")
    user = ForeignKeyField(User, backref="feature_ownership", on_delete="RESTRICT")

    class Meta:
        table_name = "feature_owner"


class FeatureVersionOwner(TimestampedModel):
    version = ForeignKeyField(FeatureVersion, unique=True, backref="ownership", on_delete="CASCADE")
    user = ForeignKeyField(User, backref="feature_version_ownership", on_delete="RESTRICT")

    class Meta:
        table_name = "feature_version_owner"


class FeatureAssignment(TimestampedModel):
    feature = ForeignKeyField(Feature, backref="assignment_events", on_delete="CASCADE")
    actor = ForeignKeyField(User, null=True, on_delete="RESTRICT")
    owner = ForeignKeyField(User, null=True, on_delete="RESTRICT")
    previous_owner = ForeignKeyField(User, null=True, on_delete="RESTRICT")
    action = CharField()

    class Meta:
        table_name = "feature_assignment"
