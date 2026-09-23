from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from inspect import iscoroutine
from typing import Any, Callable, Generic, TypeVar, cast, get_type_hints

from typomata import BaseAction, BaseState, BaseStateMachine

from ._metadata import TransitionInfo
from ._validation import classes, compatible_inputs, require, synchronous
from .errors import AmbiguousHandlerError, DefinitionError

S = TypeVar("S", bound=BaseState)


@dataclass(frozen=True)
class _Case:
    info: TransitionInfo
    invoke: Callable[[BaseStateMachine, BaseState, BaseAction], BaseState]


class MachineReducer(Generic[S]):
    """Adapt a Typomata machine; unrelated actions preserve state identity.

    S supplies the static state contract. Runtime validation comes from transition
    annotations, not a second state/action schema or generic introspection.
    """

    def __init__(self, machine: BaseStateMachine) -> None:
        self._machine = machine
        self._cases = tuple(
            _Case(
                TransitionInfo(
                    name=str(record["name"]), sources=tuple(record["sources"]),
                    actions=tuple(record["actions"]), destinations=tuple(record["destinations"]),
                ),
                record["func"],
            )
            for record in machine.transition_map()
        )

    def _validate_field(self, allowed: tuple[type, ...], context: str) -> None:
        # Composition can check existing declarations against a dataclass field
        # without requiring another user-supplied state schema.
        for case in self._cases:
            compatible_inputs(case.info.sources, allowed, context)
            if not all(any(issubclass(dest, state) for state in allowed) for dest in case.info.destinations):
                raise DefinitionError(f"{context}: transition destination is outside the field state types")

    def __call__(self, state: S, action: BaseAction) -> S:
        require(state, (BaseState,), "reducer state")
        require(action, (BaseAction,), "reducer action")
        matches = [case for case in self._cases
                   if isinstance(state, case.info.sources) and isinstance(action, case.info.actions)]
        if not matches:
            return state
        if len(matches) > 1:
            raise AmbiguousHandlerError(
                "Ambiguous reducer handlers: " + ", ".join(case.info.name for case in matches)
            )
        # Invoke the public decorated method: Typomata still owns result validation.
        result = matches[0].invoke(self._machine, state, action)
        return cast(S, result)


@dataclass(frozen=True)
class _FieldReducer:
    name: str
    states: tuple[type, ...]
    # Different fields have different state types. Validate against the field's
    # annotation before/after crossing this deliberately erased state boundary.
    reducer: Callable[[Any, BaseAction], BaseState]


class CombinedReducer(Generic[S]):
    """Compose reducers over annotated dataclass fields, preserving no-op identity.

    Unconfigured fields are retained. All children receive the same action and
    their own original field value, in keyword order. Children may themselves be
    combined reducers. Child dispatch accepts BaseAction; handlers remain narrow.
    """

    _state_type: type[S]
    _bindings: tuple[_FieldReducer, ...]

    def __init__(
        self, state_type: type[S], /, **reducers: Callable[[Any, BaseAction], BaseState],
    ) -> None:
        if not isinstance(state_type, type) or not issubclass(state_type, BaseState) or not is_dataclass(state_type):
            raise DefinitionError("Combined state must be a BaseState dataclass class")
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
            allowed = classes(hints.get(name), BaseState, context)
            synchronous(reducer, context)
            if isinstance(reducer, MachineReducer):
                reducer._validate_field(allowed, context)
            elif isinstance(reducer, CombinedReducer):
                child_state = reducer._state_type
                if not all(issubclass(item, child_state) for item in allowed):
                    raise DefinitionError(f"{context}: reducer does not accept every declared field state")
                if not any(issubclass(child_state, item) for item in allowed):
                    raise DefinitionError(f"{context}: reducer state type exceeds the field state types")
            bindings.append(_FieldReducer(name, allowed, reducer))
        self._bindings = tuple(bindings)

    def __call__(self, state: S, action: BaseAction) -> S:
        require(state, (self._state_type,), "combined reducer state")
        require(action, (BaseAction,), "combined reducer action")
        changes: dict[str, BaseState] = {}
        for binding in self._bindings:
            previous = getattr(state, binding.name)
            context = f"{self._state_type.__qualname__}.{binding.name}"
            require(previous, binding.states, f"{context} input")
            result = binding.reducer(previous, action)
            if iscoroutine(result):
                result.close()
            require(result, binding.states, f"{context} result")
            if result is not previous:
                changes[binding.name] = result
        if not changes:
            return state
        # Dataclasses' typing cannot express 'S is a dataclass'. The constructor
        # checked that constraint; replace preserves its concrete class.
        return cast(S, replace(cast(Any, state), **changes))


def combine_reducers(
    state_type: type[S], /, **reducers: Callable[[Any, BaseAction], BaseState],
) -> CombinedReducer[S]:
    """Infer the root state type while wiring reducers to named dataclass fields."""
    return CombinedReducer(state_type, **reducers)
