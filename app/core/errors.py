"""API error exception types, mapped to HTTP status in create_app."""


class ApiError(Exception):
    def __init__(self, message: str = "error", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ValidationError(ApiError):
    def __init__(self, message: str = "validation error"):
        super().__init__(message, 400)


class NotFoundError(ApiError):
    def __init__(self, message: str = "not found"):
        super().__init__(message, 404)


class ConflictError(ApiError):
    def __init__(self, message: str = "conflict"):
        super().__init__(message, 409)
