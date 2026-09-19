from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import wraps
from inspect import Parameter, Signature, isfunction, signature
from typing import Any, Callable, Generic, TypeVar, cast, get_origin, get_type_hints

from typing_extensions import ParamSpec, TypeAlias
from typomata import BaseAction, BaseState

from ._validation import classes, compatible_inputs, require, returns_none, synchronous
from .errors import AmbiguousHandlerError, DefinitionError, DispatchError

S = TypeVar("S", bound=BaseState)
A = TypeVar("A", bound=BaseAction)
P = ParamSpec("P")
Dispatch: TypeAlias = Callable[[A], None]


@dataclass(frozen=True)
class StoreAPI(Generic[S, A]):
    """A store's read/dispatch capabilities, without direct state mutation."""

    get_state: Callable[[], S]
    dispatch: Dispatch[A]


@dataclass(frozen=True)
class MiddlewareContext(StoreAPI[S, A]):
    """Per-invocation context; next is usable once, before the handler returns.

    dispatch can be retained by application-owned asynchronous work. It must be
    called on the store's owning thread.
    """

    next: Dispatch[A]


MiddlewareFactory: TypeAlias = Callable[[StoreAPI[S, A], Dispatch[A]], Dispatch[A]]


@dataclass(frozen=True)
class _Handler:
    actions: tuple[type, ...]
    original: Callable[..., None]
    name: str

    def invoke(self, receiver: object, action: object, ctx: object) -> None:
        require(action, self.actions, self.name)
        if not isinstance(ctx, MiddlewareContext):
            raise TypeError(f"{self.name}: expected MiddlewareContext")
        returns_none(cast(Callable[..., object], self.original)(receiver, action, ctx), self.name)


@dataclass
class _Declaration:
    original: Callable[..., None]
    signature: Signature | None
    definitions: dict[type, _Handler]


def _signature(func: object) -> Signature | None:
    if isfunction(func):
        if sys.version_info >= (3, 14):
            from annotationlib import Format

            return signature(func, annotation_format=Format.STRING)
        return signature(func)
    return None


def intercept(func: Callable[P, None]) -> Callable[P, None]:
    """Select a synchronous instance method by its action annotation."""
    declaration = _Declaration(func, _signature(func), {})

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        if declaration.signature is None:
            raise DefinitionError("An interceptor must be an instance method")
        receiver, action, ctx = declaration.signature.bind(*args, **kwargs).arguments.values()
        for owner in type(receiver).__mro__:
            if owner in declaration.definitions:
                declaration.definitions[owner].invoke(receiver, action, ctx)
                return
        raise DefinitionError(f"Unregistered interceptor {func.__qualname__}")

    setattr(wrapper, "__typomata_interceptor__", declaration)
    return wrapper


def _declaration(member: object) -> _Declaration | None:
    value = getattr(member, "__typomata_interceptor__", None)
    return value if isinstance(value, _Declaration) else None


def _resolve(owner: type, name: str, decl: _Declaration) -> _Handler:
    context = f"{owner.__qualname__}.{name}"
    if not isfunction(decl.original) or decl.signature is None:
        raise DefinitionError(f"{context}: expected an instance method")
    synchronous(decl.original, context)
    parameters = tuple(decl.signature.parameters.values())
    if len(parameters) != 3 or any(
        param.kind not in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)
        or param.default is not Parameter.empty for param in parameters
    ):
        raise DefinitionError(f"{context}: expected three required positional parameters (self, action, ctx)")
    try:
        hints = get_type_hints(decl.original, localns=dict(vars(owner)), include_extras=True)
    except Exception as error:
        raise DefinitionError(f"{context}: cannot resolve annotations: {error}") from error
    actions = classes(hints.get(parameters[1].name), BaseAction, context)
    ctx = hints.get(parameters[2].name)
    if get_origin(ctx) is not MiddlewareContext:
        raise DefinitionError(f"{context}: context must be annotated MiddlewareContext[S, A]")
    if hints.get("return") is not type(None):
        raise DefinitionError(f"{context}: return annotation must be None")
    return _Handler(actions, decl.original, context)


class Middleware(Generic[S, A]):
    """Action-selected handlers, composed in the store's declared order."""

    _handlers: tuple[_Handler, ...] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        members: dict[str, tuple[type, object]] = {}
        for owner in cls.__mro__:
            for name, member in vars(owner).items():
                members.setdefault(name, (owner, member))
        handlers = []
        seen: set[int] = set()
        action_handlers: dict[type, _Handler] = {}
        pending = []
        for name, (owner, member) in members.items():
            if isinstance(member, (staticmethod, classmethod, property)):
                underlying = member.fget if isinstance(member, property) else member.__func__
                if _declaration(underlying) is not None:
                    raise DefinitionError(f"{owner.__qualname__}.{name}: expected an instance method")
            decl = _declaration(member)
            if decl is None or id(decl) in seen:
                continue
            seen.add(id(decl))
            handler = decl.definitions.get(owner) or _resolve(owner, name, decl)
            for action in handler.actions:
                if action in action_handlers:
                    raise DefinitionError(f"{cls.__qualname__}: duplicate interceptor for {action.__qualname__}")
                action_handlers[action] = handler
            pending.append((decl, owner, handler))
            handlers.append(handler)
        for decl, owner, handler in pending:
            decl.definitions[owner] = handler
        cls._handlers = tuple(handlers)

    def _validate_actions(self, actions: tuple[type, ...]) -> None:
        for handler in self._handlers:
            compatible_inputs(handler.actions, actions, handler.name)

    def __call__(self, api: StoreAPI[S, A], next_dispatch: Dispatch[A]) -> Dispatch[A]:
        def dispatch(action: A) -> None:
            matches = [handler for handler in self._handlers if isinstance(action, handler.actions)]
            if not matches:
                next_dispatch(action)
                return
            if len(matches) > 1:
                raise AmbiguousHandlerError(
                    "Ambiguous middleware handlers: " + ", ".join(handler.name for handler in matches)
                )
            active = True
            forwarded = False

            def next_once(replacement: A) -> None:
                nonlocal forwarded
                if not active:
                    raise DispatchError("next() cannot be called after its handler returns")
                if forwarded:
                    raise DispatchError("next() can be called only once per handler")
                forwarded = True
                next_dispatch(replacement)

            ctx = MiddlewareContext(api.get_state, api.dispatch, next_once)
            try:
                matches[0].invoke(self, action, ctx)
            finally:
                active = False

        return dispatch
