from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from inspect import Parameter, isfunction, ismethod, signature
from typing import Any, Callable, Generic, TypeVar, cast, get_type_hints

from ._metadata import TransitionInfo
from ._validation import classes, require, synchronous, synchronous_result
from .errors import DefinitionError

S = TypeVar("S")
A = TypeVar("A")


class FunctionReducer(Generic[S]):
    """Adapt an annotated function or bound method to accept unrelated actions.

    Store and combine_reducers apply this automatically. The function must declare
    two positional parameters (state, action) and a result using concrete classes,
    unions, or Annotated.
    Action matching includes subclasses. Unmatched actions preserve state identity.
    State/result declarations are validated; annotations are resolved once.
    """

    def __init__(self, reducer: Callable[[S, A], S]) -> None:
        # Keep the callable's generic contract when inspect narrows the other
        # reference to an unparameterized function/method type.
        typed_reducer = reducer
        synchronous(reducer, "function reducer")
        if not (isfunction(reducer) or ismethod(reducer)):
            raise DefinitionError("FunctionReducer expects an annotated function or bound method")
        name = f"{reducer.__module__}.{reducer.__qualname__}"
        try:
            parameters = tuple(signature(reducer).parameters.values())
            owner = getattr(reducer, "__self__", None)
            owner_type = owner if isinstance(owner, type) else type(owner)
            hints = get_type_hints(reducer, localns=dict(vars(owner_type)), include_extras=True)
        except Exception as error:
            raise DefinitionError(f"{name}: cannot resolve reducer annotations: {error}") from error
        if len(parameters) != 2 or any(
            param.kind not in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)
            or param.default is not Parameter.empty for param in parameters
        ):
            raise DefinitionError(f"{name}: expected two required positional parameters (state, action)")
        self._info = TransitionInfo(
            name=name,
            sources=classes(hints.get(parameters[0].name), f"{name} state"),
            actions=classes(hints.get(parameters[1].name), f"{name} action"),
            destinations=classes(hints.get("return"), f"{name} result"),
        )
        if not all(any(issubclass(dest, source) for source in self._info.sources)
                   for dest in self._info.destinations):
            raise DefinitionError(f"{name}: function result is outside its accepted state types")

        def invoke(state: S, action: object) -> S:
            return typed_reducer(state, cast(A, action))

        self._invoke = invoke

    def _validate_field(self, allowed: tuple[type, ...], context: str) -> None:
        if not all(any(issubclass(state, source) for source in self._info.sources) for state in allowed):
            raise DefinitionError(f"{context}: function does not accept every declared field state")
        if not all(any(issubclass(dest, state) for state in allowed) for dest in self._info.destinations):
            raise DefinitionError(f"{context}: function result is outside the field state types")

    def __call__(self, state: S, action: object) -> S:
        self._validate_state(state)
        if not isinstance(action, self._info.actions):
            return state
        result = self._invoke(state, action)
        synchronous_result(result, f"{self._info.name} result")
        require(result, self._info.destinations, f"{self._info.name} result")
        return result

    def _validate_state(self, state: object) -> None:
        require(state, self._info.sources, f"{self._info.name} state")


@dataclass(frozen=True)
class _FieldReducer:
    name: str
    states: tuple[type, ...]
    # Different fields have different state types. Validate against the field's
    # annotation before/after crossing this deliberately erased state boundary.
    reducer: FunctionReducer[Any] | CombinedReducer[Any]


class CombinedReducer(Generic[S]):
    """Compose reducers over annotated dataclass fields, preserving no-op identity.

    Unconfigured fields are retained. All children receive the same action and
    their own original field value, in keyword order. Children may themselves be
    combined reducers. Functions are routed by their action annotations;
    unrelated actions are no-ops.
    """

    _state_type: type[S]
    _bindings: tuple[_FieldReducer, ...]

    def __init__(
        self, state_type: type[S], /, **reducers: Callable[[Any, Any], Any],
    ) -> None:
        if not isinstance(state_type, type) or not is_dataclass(state_type):
            raise DefinitionError("Combined state must be a dataclass class")
        self._state_type = state_type
        available = {field.name: field for field in fields(state_type)}
        try:
            hints = get_type_hints(state_type, include_extras=True)
        except Exception as error:
            raise DefinitionError(f"{state_type.__qualname__}: cannot resolve field annotations: {error}") from error
        bindings: list[_FieldReducer] = []
        for name, reducer in reducers.items():
            context = f"{state_type.__qualname__}.{name}"
            if name not in available:
                raise DefinitionError(f"{context}: unknown dataclass field")
            if not available[name].init:
                raise DefinitionError(f"{context}: cannot reduce a field with init=False")
            allowed = classes(hints.get(name), context)
            try:
                adapted = _adapt(reducer)
            except DefinitionError as error:
                raise DefinitionError(f"{context}: {error}") from error
            if isinstance(adapted, FunctionReducer):
                adapted._validate_field(allowed, context)
            else:
                child_state = adapted._state_type
                if not all(issubclass(item, child_state) for item in allowed):
                    raise DefinitionError(f"{context}: reducer does not accept every declared field state")
                if not any(issubclass(child_state, item) for item in allowed):
                    raise DefinitionError(f"{context}: reducer state type exceeds the field state types")
            bindings.append(_FieldReducer(name, allowed, adapted))
        self._bindings = tuple(bindings)

    def __call__(self, state: S, action: object) -> S:
        require(state, (self._state_type,), "combined reducer state")
        changes: dict[str, object] = {}
        for binding in self._bindings:
            previous = getattr(state, binding.name)
            context = f"{self._state_type.__qualname__}.{binding.name}"
            require(previous, binding.states, f"{context} input")
            result = binding.reducer(previous, action)
            synchronous_result(result, f"{context} result")
            require(result, binding.states, f"{context} result")
            if result is not previous:
                changes[binding.name] = result
        if not changes:
            return state
        # Dataclasses' typing cannot express 'S is a dataclass'. The constructor
        # checked that constraint; replace preserves its concrete class.
        return cast(S, replace(cast(Any, state), **changes))

    def _validate_state(self, state: object) -> None:
        require(state, (self._state_type,), "combined reducer state")
        for binding in self._bindings:
            value = getattr(state, binding.name)
            require(value, binding.states, f"{self._state_type.__qualname__}.{binding.name} input")
            binding.reducer._validate_state(value)


def _adapt(reducer: Callable[[S, A], S]) -> FunctionReducer[S] | CombinedReducer[S]:
    if isinstance(reducer, (FunctionReducer, CombinedReducer)):
        return reducer
    return FunctionReducer(reducer)


def _registered_actions(reducer: FunctionReducer[Any] | CombinedReducer[Any]) -> tuple[type, ...]:
    if isinstance(reducer, FunctionReducer) and type(reducer).__call__ is FunctionReducer.__call__:
        return reducer._info.actions
    if isinstance(reducer, CombinedReducer) and type(reducer).__call__ is CombinedReducer.__call__:
        return tuple(dict.fromkeys(
            action for binding in reducer._bindings for action in _registered_actions(binding.reducer)
        ))
    raise DefinitionError("required_actions cannot verify a reducer with custom __call__ behavior")


def combine_reducers(
    state_type: type[S], /, **reducers: Callable[[Any, Any], Any],
) -> CombinedReducer[S]:
    """Infer the root state type while wiring reducers to named dataclass fields."""
    return CombinedReducer(state_type, **reducers)
