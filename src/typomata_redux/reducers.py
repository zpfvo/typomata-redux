from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar, cast

from typomata import BaseAction, BaseState, BaseStateMachine

from ._validation import classes, compatible_inputs, require
from .errors import AmbiguousHandlerError, DefinitionError

S = TypeVar("S", bound=BaseState)
A = TypeVar("A", bound=BaseAction)


@dataclass(frozen=True)
class _Case:
    sources: tuple[type, ...]
    actions: tuple[type, ...]
    invoke: Callable[[BaseStateMachine, BaseState, BaseAction], BaseState]
    name: str


class MachineReducer(Generic[S, A]):
    """Adapt public Typomata transition snapshots to an identity-preserving reducer.

    Supply generic parameters explicitly: runtime union declarations are validated
    schemas, not a way for a type checker to infer this adapter's type parameters.
    """

    def __init__(
        self, machine: BaseStateMachine, *, states: object, actions: object,
    ) -> None:
        self._machine = machine
        self._states = classes(states, BaseState, "reducer states")
        self._actions = classes(actions, BaseAction, "reducer actions")
        cases = []
        for record in machine.transition_map():
            name = str(record["name"])
            sources = tuple(record["sources"])
            accepted = tuple(record["actions"])
            compatible_inputs(sources, self._states, name)
            compatible_inputs(accepted, self._actions, name)
            for destination in record["destinations"]:
                if not any(issubclass(destination, state) for state in self._states):
                    raise DefinitionError(f"{name}: destination is outside the state vocabulary")
            cases.append(_Case(sources, accepted, record["func"], name))
        self._cases = tuple(cases)

    def __call__(self, state: S, action: A) -> S:
        require(state, self._states, "reducer state")
        require(action, self._actions, "reducer action")
        matches = [case for case in self._cases
                   if isinstance(state, case.sources) and isinstance(action, case.actions)]
        if not matches:
            return state
        if len(matches) > 1:
            raise AmbiguousHandlerError(
                "Ambiguous reducer handlers: " + ", ".join(case.name for case in matches)
            )
        # Invoke the public decorated method: Typomata still owns result validation.
        result = matches[0].invoke(self._machine, state, action)
        require(result, self._states, "reducer result")
        return cast(S, result)
