from __future__ import annotations

from threading import get_ident
from typing import Callable, Generic, Iterable, TypeVar, cast

from ._recovery import call_boundary
from ._validation import returns_none, synchronous, synchronous_result
from .errors import DispatchError
from .middleware import Dispatch, MiddlewareFactory, StoreAPI
from .reducers import _adapt

S = TypeVar("S")
A = TypeVar("A")
R = TypeVar("R")


class Store(Generic[S, A]):
    """A synchronous store owned by the thread that constructed it.

    S and A are static contracts; states and actions need no marker base classes.
    The store does not runtime-enforce generic arguments. Annotated reducer
    functions are adapted just as in composition: unrelated actions are no-ops,
    and declared state/results are validated. Validate external data before dispatch.
    """

    def __init__(
        self, *, initial_state: S, reducer: Callable[[S, R], S],
        middleware: Iterable[MiddlewareFactory[S, A]] = (),
    ) -> None:
        adapted = _adapt(reducer)
        adapted._validate_state(initial_state)
        self._state = initial_state
        self._reducer = adapted
        self._owner = get_ident()
        self._reducing = False
        self._ready = False
        self._listeners: dict[object, Callable[[], None]] = {}
        api = StoreAPI(self.get_state, self.dispatch)
        self._middleware = tuple(middleware)
        dispatch: Dispatch[A] = self._checked(self._reduce)
        for factory in reversed(self._middleware):
            synchronous(factory, "middleware factory")
            handler = factory(api, dispatch)
            synchronous(handler, "middleware dispatch")
            dispatch = self._checked(handler)
        self._dispatch = dispatch
        self._ready = True

    def _check_access(self) -> None:
        if get_ident() != self._owner:
            raise DispatchError("Use the store only from its owning thread")
        if self._reducing:
            raise DispatchError("Reducers cannot access or dispatch through the store")

    def _checked(self, handler: Dispatch[A]) -> Dispatch[A]:
        def dispatch(action: A) -> None:
            self._check_access()
            if not self._ready:
                raise DispatchError("Cannot dispatch while constructing middleware")
            returns_none(cast(Callable[[A], object], handler)(action), "middleware dispatch")
        return dispatch

    def get_state(self) -> S:
        self._check_access()
        return self._state

    def dispatch(self, action: A) -> None:
        with call_boundary():
            self._check_access()
            if not self._ready:
                raise DispatchError("Cannot dispatch while constructing middleware")
            self._dispatch(action)

    def _reduce(self, action: A) -> None:
        self._reducing = True
        try:
            candidate = self._reducer(self._state, action)
            synchronous_result(candidate, "reducer result")
            self._reducer._validate_state(candidate)
            self._state = candidate
        finally:
            self._reducing = False
        for listener in tuple(self._listeners.values()):
            returns_none(listener(), "subscriber")

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._check_access()
        synchronous(listener, "subscriber")
        token = object()
        self._listeners[token] = listener

        def unsubscribe() -> None:
            self._check_access()
            self._listeners.pop(token, None)

        return unsubscribe
