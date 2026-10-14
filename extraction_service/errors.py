class ServiceError(Exception):
    def __init__(self, code: str, status: int = 422, retryable: bool = False, retry_after: int | None = None):
        # Messages intentionally contain no source/provider content.
        self.code, self.status = code, status
        self.retryable, self.retry_after = retryable, retry_after
        super().__init__(code)

    def payload(self):
        return {
            "code": self.code,
            "message": self.code.replace("_", " "),
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after,
        }
