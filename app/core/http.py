from flask import request

from app.core.errors import ValidationError


def json_object():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("request body must be a JSON object")
    return data
