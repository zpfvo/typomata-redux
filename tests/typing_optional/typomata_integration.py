"""The optional adapter preserves compatibility with Typomata's public map."""
from dataclasses import dataclass
from typing_extensions import assert_type
from typomata import BaseAction, BaseState, BaseStateMachine, transition
from typomata_redux import MachineReducer, Store, combine_reducers


@dataclass(frozen=True)
class Count(BaseState):
    value: int = 0


class Add(BaseAction):
    pass


@dataclass(frozen=True)
class Root:
    count: Count = Count()


class Counter(BaseStateMachine):
    @transition
    def add(self, state: Count, action: Add) -> Count:
        return Count(state.value + 1)


machine = MachineReducer[Count](Counter())
assert_type(machine(Count(), Add()), Count)
assert_type(machine(Count(), object()), Count)
root = combine_reducers(Root, count=machine)
assert_type(root(Root(), Add()), Root)
store = Store[Root, Add](initial_state=Root(), reducer=root)
machine(Root(), Add())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
store.dispatch(object())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
