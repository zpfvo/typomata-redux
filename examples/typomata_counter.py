"""Optional Typomata integration: machine reducers alongside the core store."""
from __future__ import annotations

from dataclasses import dataclass

from typomata import BaseAction, BaseState, BaseStateMachine, transition
from typomata_redux import MachineReducer, Middleware, Store, StoreAPI, combine_reducers, intercept_pre, intercept_post


@dataclass(frozen=True)
class Add(BaseAction):
    amount: int


@dataclass(frozen=True)
class Remember(BaseAction):
    value: int


@dataclass(frozen=True)
class NoOp(BaseAction):
    pass


Actions = Add | Remember | NoOp


@dataclass(frozen=True)
class Count(BaseState):
    value: int = 0


@dataclass(frozen=True)
class History(BaseState):
    values: tuple[int, ...] = ()


@dataclass(frozen=True)
class AppState(BaseState):
    count: Count = Count()
    history: History = History()


class Counter(BaseStateMachine):
    @transition
    def add(self, state: Count, action: Add) -> Count:
        return Count(state.value + action.amount)


class Recorder(BaseStateMachine):
    @transition
    def remember(self, state: History, action: Remember) -> History:
        return History((*state.values, action.value))


counter = MachineReducer[Count](Counter())
recorder = MachineReducer[History](Recorder())


reduce = combine_reducers(AppState, count=counter, history=recorder)


class Log(Middleware[AppState, Actions]):
    @intercept_pre
    def before(self, action: Actions, ctx: StoreAPI[AppState, Actions]) -> None:
        print("before", type(action).__name__, ctx.get_state())

    @intercept_post
    def after(self, action: Actions, ctx: StoreAPI[AppState, Actions]) -> None:
        print("after", type(action).__name__, ctx.get_state())


class RecordChanges(Middleware[AppState, Actions]):
    @intercept_post
    def add(self, action: Add, ctx: StoreAPI[AppState, Actions]) -> None:
        ctx.dispatch(Remember(ctx.get_state().count.value))


def main() -> None:
    store = Store[AppState, Actions](
        initial_state=AppState(), reducer=reduce,
        middleware=[Log(), RecordChanges()],
    )
    store.dispatch(Add(3))
    assert store.get_state() == AppState(Count(3), History((3,)))
    old = store.get_state()
    store.dispatch(NoOp())
    assert store.get_state() is old


if __name__ == "__main__":
    main()
