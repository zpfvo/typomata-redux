"""Internal read-only views of configured dispatch declarations.

No factories, handlers, reducers, selectors, or state accessors are executed.
Plain callables and subclasses replacing dispatch are opaque. Descriptions do not
predict consumption, replacement, nested dispatch, or whether a reducer is reached.
This is preparation for static diagrams, not a public inspection API.
"""
from __future__ import annotations

from inspect import isfunction, ismethod
from typing import Any

from ._metadata import FieldInfo, MiddlewareInfo, ReducerInfo, StoreInfo
from .middleware import Middleware
from .reducers import CombinedReducer, FunctionReducer
from .store import Store


def _name(value: object) -> str:
    target = value if isfunction(value) or ismethod(value) else type(value)
    return f"{target.__module__}.{target.__qualname__}"


def describe_reducer(reducer: object) -> ReducerInfo:
    if isinstance(reducer, FunctionReducer) and type(reducer).__call__ is FunctionReducer.__call__:
        return ReducerInfo(name=reducer._info.name, kind="function", transitions=(reducer._info,))
    if isinstance(reducer, CombinedReducer) and type(reducer).__call__ is CombinedReducer.__call__:
        return ReducerInfo(
            name=_name(reducer), kind="combined", state_type=reducer._state_type,
            fields=tuple(FieldInfo(binding.name, binding.states, describe_reducer(binding.reducer))
                         for binding in reducer._bindings),
        )
    return ReducerInfo(name=_name(reducer), kind="opaque")


def describe_middleware(middleware: object) -> MiddlewareInfo:
    if isinstance(middleware, Middleware) and type(middleware).__call__ is Middleware.__call__:
        return MiddlewareInfo(
            name=_name(middleware), kind="annotated",
            handlers=tuple(handler.info for handler in middleware._handlers),
            coverage=middleware._coverage,
        )
    return MiddlewareInfo(name=_name(middleware), kind="opaque")


def describe_store(store: Store[Any, Any]) -> StoreInfo:
    # Keep configured order, including repeated instances of the same middleware.
    return StoreInfo(
        middleware=tuple(describe_middleware(factory) for factory in store._middleware),
        reducer=describe_reducer(store._reducer),
        required_actions=store._required_actions,
    )
