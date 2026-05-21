"""Runtime hooks for surfacing agent progress and live user messages to UIs."""

from contextvars import ContextVar
from typing import Any, Callable

EventCallback = Callable[[dict[str, Any]], None]
UserMessageProvider = Callable[[], list[dict[str, Any]]]
CancelProvider = Callable[[], bool]


class WorkflowCancelled(RuntimeError):
    """Raised when a running workflow is stopped by the user."""

_event_callback: ContextVar[EventCallback | None] = ContextVar("event_callback", default=None)
_user_message_provider: ContextVar[UserMessageProvider | None] = ContextVar(
    "user_message_provider",
    default=None,
)
_cancel_provider: ContextVar[CancelProvider | None] = ContextVar(
    "cancel_provider",
    default=None,
)


def set_event_callback(callback: EventCallback | None):
    """Set the event callback for the current execution context."""
    return _event_callback.set(callback)


def reset_event_callback(token):
    """Reset the event callback context variable."""
    _event_callback.reset(token)


def emit_event(event: dict[str, Any]):
    """Emit an event to the current callback, if one is configured."""
    callback = _event_callback.get()
    if callback is not None:
        callback(event)


def set_user_message_provider(provider: UserMessageProvider | None):
    """Set the live user message provider for the current execution context."""
    return _user_message_provider.set(provider)


def reset_user_message_provider(token):
    """Reset the live user message provider context variable."""
    _user_message_provider.reset(token)


def get_user_messages() -> list[dict[str, Any]]:
    """Return live user messages available to the current workflow run."""
    provider = _user_message_provider.get()
    if provider is None:
        return []
    return provider()


def set_cancel_provider(provider: CancelProvider | None):
    """Set the cancellation provider for the current execution context."""
    return _cancel_provider.set(provider)


def reset_cancel_provider(token):
    """Reset the cancellation provider context variable."""
    _cancel_provider.reset(token)


def is_cancel_requested() -> bool:
    """Return whether the current workflow has been asked to stop."""
    provider = _cancel_provider.get()
    return bool(provider and provider())


def check_cancelled():
    """Raise when the current workflow has been asked to stop."""
    if is_cancel_requested():
        raise WorkflowCancelled("任务已停止")
