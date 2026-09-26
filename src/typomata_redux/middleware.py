from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from functools import wraps
from inspect import Parameter, Signature, isfunction, signature
from typing import Any, Callable, Generic, Literal, TypeVar, cast, get_args, get_origin, get_type_hints, overload

from typing_extensions import ParamSpec, TypeAlias

from ._contracts import agree, concrete, inheritance, members
from ._metadata import InterceptorInfo, Phase, PhaseCoverage
from ._recovery import Invocation, protect
from ._validation import classes, require, returns_none, synchronous
from .errors import AmbiguousHandlerError, CancelAction, DefinitionError, DispatchError, MiddlewareError

S = TypeVar("S")
A = TypeVar("A")
P = ParamSpec("P")
Dispatch: TypeAlias = Callable[[A], None]
_Outcome = Literal["returned", "recovered", "cancelled"]
_logger = logging.getLogger(__name__)
_UNSET = object()


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
    info: InterceptorInfo
    original: Callable[..., None]
    owner: type
    context_state: object
    context_actions: object

    def _validate(self, action: object, ctx: object) -> None:
        require(action, self.info.actions, self.info.name)
        expected = MiddlewareContext if self.info.phase == "manual" else StoreAPI
        if not isinstance(ctx, expected):
            raise TypeError(f"{self.info.name}: expected {expected.__name__}")

    def invoke(self, receiver: object, action: object, ctx: object) -> None:
        # Direct calls (including super()) keep ordinary Python exception behavior.
        self._validate(action, ctx)
        returns_none(cast(Callable[..., object], self.original)(receiver, action, ctx), self.info.name)

    def run(self, receiver: object, action: object, ctx: object, scope: Invocation) -> _Outcome:
        self._validate(action, ctx)
        try:
            result = cast(Callable[..., object], self.original)(receiver, action, ctx)
        except CancelAction:
            if scope.failed_call:
                raise
            return "cancelled"
        except (MiddlewareError, DefinitionError, DispatchError, AmbiguousHandlerError):
            raise
        except Exception:
            if not self.info.catch_exceptions or scope.failed_call:
                raise
            _logger.exception("Recovering from %s (%s) handling %s", self.info.name, self.info.phase, type(action).__qualname__)
            return "recovered"
        # Return-contract errors are infrastructure failures, not recoverable effects.
        returns_none(result, self.info.name)
        return "returned"


@dataclass
class _Declaration:
    original: Callable[..., None]
    signature: Signature | None
    definitions: dict[type, _Handler]
    phase: Phase
    catch_exceptions: bool


def _signature(func: object) -> Signature | None:
    if isfunction(func):
        if sys.version_info >= (3, 14):
            from annotationlib import Format

            return signature(func, annotation_format=Format.STRING)
        return signature(func)
    return None


def _decorate(func: Callable[P, None], phase: Phase, catch_exceptions: bool) -> Callable[P, None]:
    if _declaration(func) is not None:
        raise DefinitionError("Use one interceptor decorator per method")
    declaration = _Declaration(func, _signature(func), {}, phase, catch_exceptions)

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


def _configure(
    func: Callable[P, None] | None, phase: Phase, catch_exceptions: bool,
) -> Callable[P, None] | Callable[[Callable[P, None]], Callable[P, None]]:
    if not isinstance(catch_exceptions, bool):
        raise DefinitionError("catch_exceptions must be a bool")

    def decorate(method: Callable[P, None]) -> Callable[P, None]:
        return _decorate(method, phase, catch_exceptions)

    return decorate if func is None else decorate(func)


@overload
def intercept(func: Callable[P, None], *, catch_exceptions: bool = False) -> Callable[P, None]: ...


@overload
def intercept(func: None = None, *, catch_exceptions: bool = False) -> Callable[[Callable[P, None]], Callable[P, None]]: ...


def intercept(
    func: Callable[P, None] | None = None, *, catch_exceptions: bool = False,
) -> Callable[P, None] | Callable[[Callable[P, None]], Callable[P, None]]:
    """Handle an action with explicit forwarding through MiddlewareContext.next.

    Returning without next, or raising CancelAction, consumes the action and
    returns normally to earlier middleware. Earlier post-handlers still run.
    Consumption cannot undo forwarding, committed state, or previous effects.

    catch_exceptions=True logs ordinary handler failures with a traceback. Before
    next it then forwards the original action; after successful next it returns
    without forwarding again. A successful handler that omits next still consumes.
    Context-call failures and MiddlewareError always propagate. Default: False.
    Recovery/cancellation handling applies to chain dispatch, not direct calls.
    """
    return _configure(func, "manual", catch_exceptions)


@overload
def intercept_pre(func: Callable[P, None], *, catch_exceptions: bool = False) -> Callable[P, None]: ...


@overload
def intercept_pre(func: None = None, *, catch_exceptions: bool = False) -> Callable[[Callable[P, None]], Callable[P, None]]: ...


def intercept_pre(
    func: Callable[P, None] | None = None, *, catch_exceptions: bool = False,
) -> Callable[P, None] | Callable[[Callable[P, None]], Callable[P, None]]:
    """Run before automatically forwarding; context is StoreAPI, without next.

    catch_exceptions=True logs ordinary handler failures and forwards normally.
    CancelAction consumes instead: skip downstream and this middleware's post
    handler, but unwind normally to earlier middleware. Context-call failures and
    MiddlewareError propagate. Default: False; direct calls do not recover.
    """
    return _configure(func, "pre", catch_exceptions)


@overload
def intercept_post(func: Callable[P, None], *, catch_exceptions: bool = False) -> Callable[P, None]: ...


@overload
def intercept_post(func: None = None, *, catch_exceptions: bool = False) -> Callable[[Callable[P, None]], Callable[P, None]]: ...


def intercept_post(
    func: Callable[P, None] | None = None, *, catch_exceptions: bool = False,
) -> Callable[P, None] | Callable[[Callable[P, None]], Callable[P, None]]:
    """Run after downstream returns successfully, including consumed actions.

    Context is StoreAPI, without next. This is not a guarantee of reduction.
    catch_exceptions=True logs ordinary handler failures and returns normally.
    CancelAction also ends this handler normally; neither can undo downstream work.
    Context-call failures and MiddlewareError propagate. Default: False;
    direct calls do not recover.
    """
    return _configure(func, "post", catch_exceptions)



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
    actions = classes(hints.get(parameters[1].name), context)
    ctx = hints.get(parameters[2].name)
    expected = MiddlewareContext if decl.phase == "manual" else StoreAPI
    if get_origin(ctx) is not expected:
        raise DefinitionError(f"{context}: context must be annotated {expected.__name__}[S, A]")
    if hints.get("return") is not type(None):
        raise DefinitionError(f"{context}: return annotation must be None")
    return _Handler(
        InterceptorInfo(context, actions, decl.phase, decl.catch_exceptions), decl.original,
        owner, *get_args(ctx),
    )


def _phase_coverage(owner: type, phase: Phase, declaration: object) -> PhaseCoverage:
    if declaration is _UNSET:
        # Merge direct-base contracts, deduplicating diamonds. An explicit
        # declaration replaces the inherited vocabulary for this phase.
        actions = tuple(dict.fromkeys(
            action
            for base in owner.__bases__ if issubclass(base, Middleware)
            for coverage in base._coverage if coverage.phase == phase
            for action in coverage.actions
        ))
    else:
        actions = () if declaration is None else classes(declaration, f"{owner.__qualname__}.{phase}_actions")
    return PhaseCoverage(phase, actions)


def _validate_coverage(owner: type, coverage: tuple[PhaseCoverage, ...], handlers: list[_Handler]) -> None:
    for contract in coverage:
        label = f"{owner.__qualname__}.{contract.phase}_actions"
        registered = [handler for handler in handlers if handler.info.phase == contract.phase]
        if registered and not contract.actions:
            raise DefinitionError(f"{label}: declare an action class or union for this phase's handlers")
        for handler in registered:
            for action in handler.info.actions:
                if not any(issubclass(action, allowed) for allowed in contract.actions):
                    raise DefinitionError(f"{handler.info.name}: {action.__qualname__} is outside declared {label}")
        for action in contract.actions:
            if not any(any(issubclass(action, accepted) for accepted in handler.info.actions)
                       for handler in registered):
                raise DefinitionError(f"{owner.__qualname__}: missing {contract.phase} handler for {action.__qualname__}")

    # Check all named types, including narrower handler inputs under a declared
    # superclass. Future multiple-inheritance subclasses still need dispatch-time
    # ambiguity checks; this is not a proof about every possible Python class.
    known_actions = dict.fromkeys(
        [action for contract in coverage for action in contract.actions]
        + [action for handler in handlers for action in handler.info.actions]
    )
    for action in known_actions:
        matches = [handler for handler in handlers
                   if any(issubclass(action, accepted) for accepted in handler.info.actions)]
        phases = [handler.info.phase for handler in matches]
        if len(phases) != len(set(phases)) or ("manual" in phases and len(phases) > 1):
            raise DefinitionError(
                f"{owner.__qualname__}: conflicting interceptors for {action.__qualname__}: "
                + ", ".join(handler.info.name for handler in matches)
            )


def _validate_owner(
    owner: object, handlers: tuple[_Handler, ...], coverage: tuple[PhaseCoverage, ...],
    *, binding: bool = False,
) -> None:
    cls = get_origin(owner) or owner
    label = getattr(cls, '__qualname__', 'Middleware')
    paths = inheritance(owner, Middleware)
    contracts = [(members(S, path), members(A, path)) for path in paths.get(Middleware, [{}])]
    for state, actions in contracts:
        declared_state = concrete(state, f'{label} state')
        declared_actions = concrete(actions, f'{label} actions')
        if handlers and (declared_state is None or declared_actions is None):
            if binding or not getattr(cls, '__parameters__', ()):
                raise DefinitionError(f'{label}: specify concrete Middleware[S, A] arguments before binding')
        for other_state, other_actions in contracts:
            agree(state, other_state, f'{label} inherited state')
            agree(actions, other_actions, f'{label} inherited actions')
        if declared_actions is not None:
            for phase in coverage:
                for action in phase.actions:
                    if not any(issubclass(action, allowed) for allowed in declared_actions):
                        raise DefinitionError(
                            f'{label}.{phase.phase}_actions: {action.__qualname__} is outside Middleware action vocabulary'
                        )
        for handler in handlers:
            for path in paths.get(handler.owner, [{}]):
                for hint, expected, role in ((handler.context_state, state, 'state'),
                                             (handler.context_actions, actions, 'actions')):
                    resolved = members(hint, path)
                    context = f'{handler.info.name} context {role}'
                    if concrete(resolved, context) is None and (binding or not getattr(cls, '__parameters__', ())):
                        raise DefinitionError(f'{context}: unresolved TypeVars')
                    agree(resolved, expected, context)


class Middleware(Generic[S, A]):
    """Action-selected handlers, composed in the store's declared order.

    Declare pre_actions, post_actions, and/or manual_actions as class keywords
    for every used phase. Each class/union must be fully covered by its phase's
    handlers, whose annotations must fit that vocabulary. Validation runs at
    class definition, not in a static type checker. Unused phases may be omitted.
    Subclasses inherit contracts from their direct bases and are revalidated;
    an explicit keyword replaces that phase, and None clears it. A cleared phase
    must have no registered handlers. Constructors remain application-owned.

    Context state/action arguments must agree with Middleware[S, A]; each phase's
    vocabulary must fit A. Generic base arguments are substituted through the
    inheritance graph. Deferred generic instance contracts are checked on binding
    to a store. These are runtime definition checks, not static typing guarantees.

    Consumption stops forwarding, not normal unwinding. For example::

        Logging: pre
          Validation: returns without calling next
          (remaining middleware and reducer are skipped)
        Logging: post

    Post-handlers therefore do not prove that the action reached the reducer.
    Once next has been called, consumption cannot roll back downstream work.

    CancelAction expresses the same consumption behavior within any annotated
    handler. In a pre-handler it also skips this middleware's own post-handler.
    After next it only ends the current handler; downstream work is not undone.

    Decorators default to catch_exceptions=False. Opting in catches and logs
    ordinary handler-body failures. Context-call failures, invalid return values,
    infrastructure errors, MiddlewareError, and BaseException subclasses propagate.
    Store dispatch failures also propagate when using a retained callback or
    calling a store directly. Failures belong to the currently calling handler.
    Once a context/store call fails, later errors in that invocation also propagate,
    even if user code caught or translated the original error. Explicitly handling
    an error and returning normally remains possible. Failures handled entirely
    inside a nested dispatch do not affect its caller. No forwarding is retried.
    """

    _handlers: tuple[_Handler, ...] = ()
    _coverage: tuple[PhaseCoverage, ...] = ()

    def __init_subclass__(
        cls, *, pre_actions: object = _UNSET, post_actions: object = _UNSET,
        manual_actions: object = _UNSET, **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)
        members: dict[str, tuple[type, object]] = {}
        for owner in cls.__mro__:
            for name, member in vars(owner).items():
                members.setdefault(name, (owner, member))
        handlers = []
        seen: set[int] = set()
        action_handlers: dict[type, list[_Handler]] = {}
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
            for action in handler.info.actions:
                previous = action_handlers.setdefault(action, [])
                if any(item.info.phase == handler.info.phase or "manual" in (item.info.phase, handler.info.phase) for item in previous):
                    raise DefinitionError(f"{cls.__qualname__}: conflicting interceptors for {action.__qualname__}")
                previous.append(handler)
            pending.append((decl, owner, handler))
            handlers.append(handler)
        coverage = (
            _phase_coverage(cls, "manual", manual_actions),
            _phase_coverage(cls, "pre", pre_actions),
            _phase_coverage(cls, "post", post_actions),
        )
        _validate_coverage(cls, coverage, handlers)
        _validate_owner(cls, tuple(handlers), coverage)
        for decl, owner, handler in pending:
            decl.definitions[owner] = handler
        cls._handlers = tuple(handlers)
        cls._coverage = coverage

    def __call__(self, api: StoreAPI[S, A], next_dispatch: Dispatch[A]) -> Dispatch[A]:
        _validate_owner(getattr(self, '__orig_class__', type(self)), self._handlers, self._coverage, binding=True)

        def dispatch(action: A) -> None:
            matches = [handler for handler in self._handlers if isinstance(action, handler.info.actions)]
            if not matches:
                next_dispatch(action)
                return
            manual = [handler for handler in matches if handler.info.phase == "manual"]
            pre = [handler for handler in matches if handler.info.phase == "pre"]
            post = [handler for handler in matches if handler.info.phase == "post"]
            if len(manual) > 1 or len(pre) > 1 or len(post) > 1 or (manual and (pre or post)):
                raise AmbiguousHandlerError(
                    "Ambiguous middleware handlers: " + ", ".join(handler.info.name for handler in matches)
                )
            def run_automatic(handler: _Handler) -> _Outcome:
                scope = Invocation()
                ctx = StoreAPI(protect(api.get_state), protect(api.dispatch))
                with scope.activate():
                    return handler.run(self, action, ctx, scope)

            if not manual:
                if pre and run_automatic(pre[0]) == "cancelled":
                    return
                next_dispatch(action)
                if post:
                    run_automatic(post[0])
                return
            scope = Invocation()
            forwarded = False

            def next_once(replacement: A) -> None:
                nonlocal forwarded
                if not scope.active:
                    raise DispatchError("next() cannot be called after its handler returns")
                if forwarded:
                    raise DispatchError("next() can be called only once per handler")
                forwarded = True
                next_dispatch(replacement)

            ctx = MiddlewareContext(
                protect(api.get_state), protect(api.dispatch), protect(next_once),
            )
            with scope.activate():
                outcome = manual[0].run(self, action, ctx, scope)
                if outcome == "recovered" and not forwarded:
                    ctx.next(action)

        return dispatch
