class FoundryError(Exception):
    """An expected, machine-readable refusal or unresolved operation."""
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self):
        return {"ok": False, "code": self.code, "message": str(self), **self.details}
