"""Track failures crossing into the currently executing middleware handler."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Iterator, TypeVar

from typing_extensions import ParamSpec

P = ParamSpec("P")
R = TypeVar("R")
_current: ContextVar[Invocation | None] = ContextVar("middleware_invocation", default=None)


@dataclass
class Invocation:
    failed_call: bool = False
    active: bool = True

    @contextmanager
    def activate(self) -> Iterator[None]:
        token = _current.set(self)
        try:
            yield
        finally:
            self.active = False
            _current.reset(token)


@contextmanager
def call_boundary() -> Iterator[None]:
    # Capture the caller before nested dispatch installs its own handler scopes.
    # Only a failure escaping this call affects the caller's recovery policy.
    caller = _current.get()
    try:
        yield
    except BaseException:
        if caller is not None and caller.active:
            caller.failed_call = True
        raise


def protect(func: Callable[P, R]) -> Callable[P, R]:
    def call(*args: P.args, **kwargs: P.kwargs) -> R:
        with call_boundary():
            return func(*args, **kwargs)
    return call
