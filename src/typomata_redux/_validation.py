from __future__ import annotations

import inspect
from types import UnionType
from typing import Annotated, Any, Union, get_args, get_origin

from .errors import DefinitionError


def classes(hint: object, base: type, context: str) -> tuple[type, ...]:
    """Normalize the small runtime annotation vocabulary we support."""
    origin = get_origin(hint)
    if origin is Annotated:
        return classes(get_args(hint)[0], base, context)
    if origin in (Union, UnionType):
        return tuple(dict.fromkeys(
            member for arg in get_args(hint) for member in classes(arg, base, context)
        ))
    if (
        not isinstance(hint, type)
        or hint is Any
        or not issubclass(hint, base)
        or getattr(hint, "_is_protocol", False)
    ):
        raise DefinitionError(f"{context}: expected {base.__name__} classes or unions")
    return (hint,)


def require(value: object, allowed: tuple[type, ...], context: str) -> None:
    if not isinstance(value, allowed):
        names = ", ".join(cls.__qualname__ for cls in allowed)
        raise TypeError(f"{context}: expected {names}, got {type(value).__qualname__}")


def compatible_inputs(
    declared: tuple[type, ...], allowed: tuple[type, ...], context: str,
) -> None:
    for cls in declared:
        if not any(issubclass(cls, item) or issubclass(item, cls) for item in allowed):
            raise DefinitionError(f"{context}: {cls.__qualname__} is outside the vocabulary")


def synchronous(func: object, context: str) -> None:
    target = func if inspect.isfunction(func) or inspect.ismethod(func) else getattr(func, "__call__", func)
    if not callable(func) or any(check(target) for check in (
        inspect.iscoroutinefunction, inspect.isgeneratorfunction, inspect.isasyncgenfunction,
    )):
        raise DefinitionError(f"{context}: expected a synchronous, non-generator callable")


def returns_none(value: object, context: str) -> None:
    if value is not None:
        if inspect.iscoroutine(value):
            value.close()
        raise TypeError(f"{context}: must return None, got {type(value).__qualname__}")
