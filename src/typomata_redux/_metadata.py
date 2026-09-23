"""Internal immutable declarations shared by dispatch and inspection.

These describe registrations, not guaranteed execution or side effects. Names are
labels; a middleware position or a composition field path identifies an occurrence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Phase = Literal["manual", "pre", "post"]


@dataclass(frozen=True)
class InterceptorInfo:
    name: str
    actions: tuple[type, ...]
    phase: Phase
    catch_exceptions: bool


@dataclass(frozen=True)
class TransitionInfo:
    name: str
    sources: tuple[type, ...]
    actions: tuple[type, ...]
    destinations: tuple[type, ...]


@dataclass(frozen=True)
class FieldInfo:
    name: str
    states: tuple[type, ...]
    reducer: ReducerInfo


@dataclass(frozen=True)
class ReducerInfo:
    name: str
    kind: Literal["machine", "combined", "opaque"]
    transitions: tuple[TransitionInfo, ...] = ()
    state_type: type | None = None
    fields: tuple[FieldInfo, ...] = ()


@dataclass(frozen=True)
class MiddlewareInfo:
    name: str
    kind: Literal["annotated", "opaque"]
    handlers: tuple[InterceptorInfo, ...] = ()


@dataclass(frozen=True)
class StoreInfo:
    middleware: tuple[MiddlewareInfo, ...]
    reducer: ReducerInfo
