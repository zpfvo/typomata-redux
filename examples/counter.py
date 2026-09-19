"""Nested state, two slices, an effect middleware, and a full-chain follow-up."""
from __future__ import annotations

from dataclasses import dataclass, replace

from typomata import BaseAction, BaseState, BaseStateMachine, transition
from typomata_redux import MachineReducer, Middleware, MiddlewareContext, Store, intercept


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


counter = MachineReducer[Count, Actions](Counter(), states=Count, actions=Actions)
recorder = MachineReducer[History, Actions](Recorder(), states=History, actions=Actions)


def reduce(state: AppState, action: Actions) -> AppState:
    count = counter(state.count, action)
    history = recorder(state.history, action)
    if count is state.count and history is state.history:
        return state
    return replace(state, count=count, history=history)


class Log(Middleware[AppState, Actions]):
    @intercept
    def log(self, action: Actions, ctx: MiddlewareContext[AppState, Actions]) -> None:
        print("before", type(action).__name__, ctx.get_state())
        ctx.next(action)
        print("after", type(action).__name__, ctx.get_state())


class RecordChanges(Middleware[AppState, Actions]):
    @intercept
    def add(self, action: Add, ctx: MiddlewareContext[AppState, Actions]) -> None:
        ctx.next(action)
        ctx.dispatch(Remember(ctx.get_state().count.value))


def main() -> None:
    store = Store[AppState, Actions](
        initial_state=AppState(), reducer=reduce, states=AppState, actions=Actions,
        middleware=[Log(), RecordChanges()],
    )
    store.dispatch(Add(3))
    assert store.get_state() == AppState(Count(3), History((3,)))
    old = store.get_state()
    store.dispatch(NoOp())
    assert store.get_state() is old


if __name__ == "__main__":
    main()
