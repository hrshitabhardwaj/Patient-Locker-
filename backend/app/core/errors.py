"""Domain errors raised by services and turned into JSON responses by the app."""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFoundError(ServiceError):
    status_code = 404


class ConflictError(ServiceError):
    status_code = 409


class UnprocessableError(ServiceError):
    status_code = 422
