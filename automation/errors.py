"""Error types for the browser automation layer.

Playwright-level exceptions are caught at the action boundary and converted
into structured ActionError values (see results.py), so callers never have to
handle Playwright's exception hierarchy.
"""

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .results import ActionError

# Failure categories exposed to callers.
TIMEOUT = "timeout"
NOT_FOUND = "not_found"
SERVER_ERROR = "server_error"
VALIDATION = "validation"
NAVIGATION = "navigation"
BROWSER = "browser"
UNKNOWN = "unknown"


def from_exception(exc: Exception, action: str, page_url: str | None = None) -> ActionError:
    """Convert any exception raised during an action into a structured ActionError."""
    if isinstance(exc, PlaywrightTimeoutError):
        return ActionError(
            type=TIMEOUT,
            message=f"{action} timed out: {exc}",
            page_url=page_url,
            details={"original_type": type(exc).__name__},
        )
    if isinstance(exc, PlaywrightError):
        return ActionError(
            type=BROWSER,
            message=f"{action} failed: {exc}",
            page_url=page_url,
            details={"original_type": type(exc).__name__},
        )
    return ActionError(
        type=UNKNOWN,
        message=f"{action} failed unexpectedly: {exc}",
        page_url=page_url,
        details={"original_type": type(exc).__name__},
    )
