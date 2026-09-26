"""Resolve middleware contracts through generic inheritance, without instantiation."""
from __future__ import annotations

from types import UnionType
from typing import Annotated, TypeVar, Union, get_args, get_origin

from ._validation import classes
from .errors import DefinitionError

Members = tuple[object, ...]
Bindings = dict[TypeVar, Members]


def members(hint: object, bindings: Bindings) -> Members:
    if isinstance(hint, TypeVar):
        return bindings.get(hint, (hint,))
    origin = get_origin(hint)
    if origin is Annotated:
        return members(get_args(hint)[0], bindings)
    if origin in (Union, UnionType):
        return tuple(dict.fromkeys(
            item for argument in get_args(hint) for item in members(argument, bindings)
        ))
    return (hint,)


def inheritance(owner: object, base: type) -> dict[type, list[Bindings]]:
    """Keep each inheritance path: incompatible specializations must not be lost."""
    result: dict[type, list[Bindings]] = {}

    def visit(hint: object, bindings: Bindings) -> None:
        origin = get_origin(hint) or hint
        if not isinstance(origin, type) or not issubclass(origin, base):
            return
        parameters: tuple[TypeVar, ...] = getattr(origin, '__parameters__', ())
        arguments = get_args(hint)
        resolved: Bindings = dict(zip(parameters, (members(arg, bindings) for arg in arguments))) if arguments else bindings
        paths = result.setdefault(origin, [])
        if resolved in paths:
            return
        paths.append(resolved)
        if origin is not base:
            # getattr would reuse a parent's __orig_bases__ on ordinary subclasses.
            for parent in vars(origin).get('__orig_bases__', origin.__bases__):
                visit(parent, resolved)

    visit(owner, {})
    return result


def concrete(values: Members, context: str) -> tuple[type, ...] | None:
    resolved: list[type] = []
    deferred = False
    for value in values:
        if isinstance(value, TypeVar):
            deferred = True
        else:
            resolved.extend(classes(value, context))
    return None if deferred else tuple(dict.fromkeys(resolved))


def agree(left: Members, right: Members, context: str) -> None:
    first, second = concrete(left, context), concrete(right, context)
    if first is not None and second is not None and set(first) != set(second):
        raise DefinitionError(f'{context}: must match Middleware state and action arguments')
