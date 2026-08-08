"""Wire protocol between the server and the kernel worker subprocess.

Framing: one JSON object per line (UTF-8, ``\\n``-delimited) on stdin/stdout.
Request:  {"id": int, "method": str, "params": {...}}
Response: {"id": int, "result": {...}}
       or {"id": int, "error": {"type": str, "message": str, "details": {...}}}

The worker writes nothing but protocol lines to stdout; diagnostics go to
stderr. Error types: "script_error" (user script raised / bad syntax),
"contract_error" (PARAMS/build contract violated), "kernel_error" (OCCT or
geometry failure). "timeout" and "kernel_crash" are synthesized client-side.
"""

from __future__ import annotations

ERROR_SCRIPT = "script_error"
ERROR_CONTRACT = "contract_error"
ERROR_KERNEL = "kernel_error"
ERROR_TIMEOUT = "timeout"
ERROR_CRASH = "kernel_crash"

METHODS = (
    "ping",
    "inspect",
    "build",
    "export",
    "export_assembly",
    "interference",
    "shutdown",
)


class WorkerError(Exception):
    """Raised inside the worker; converted to an error response."""

    def __init__(self, type: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.type = type
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict:
        return {"type": self.type, "message": self.message, "details": self.details}
