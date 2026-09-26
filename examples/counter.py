"""Nested state, two slices, an effect middleware, and a full-chain follow-up."""
from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import assert_never
from typomata_redux import Middleware, Store, StoreAPI, combine_reducers, intercept_pre, intercept_post


@dataclass(frozen=True)
class Add:
    amount: int


@dataclass(frozen=True)
class Remember:
    value: int


@dataclass(frozen=True)
class NoOp:
    pass


@dataclass(frozen=True)
class Reset:
    pass


Actions = Add | Remember | NoOp | Reset


@dataclass(frozen=True)
class Count:
    value: int = 0


@dataclass(frozen=True)
class History:
    values: tuple[int, ...] = ()


@dataclass(frozen=True)
class AppState:
    count: Count = Count()
    history: History = History()


def counter(state: Count, action: Add | Reset | NoOp) -> Count:
    if isinstance(action, Add):
        return Count(state.value + action.amount)
    if isinstance(action, Reset):
        return Count()
    if isinstance(action, NoOp):
        return state
    assert_never(action)


def recorder(state: History, action: Remember | Reset) -> History:
    if isinstance(action, Remember):
        return History((*state.values, action.value))
    if isinstance(action, Reset):
        return History()
    assert_never(action)


reduce = combine_reducers(AppState, count=counter, history=recorder)

class Log(Middleware[AppState, Actions], pre_actions=Actions, post_actions=Actions):
    @intercept_pre
    def before(self, action: Actions, ctx: StoreAPI[AppState, Actions]) -> None:
        print("before", type(action).__name__, ctx.get_state())

    @intercept_post
    def after(self, action: Actions, ctx: StoreAPI[AppState, Actions]) -> None:
        print("after", type(action).__name__, ctx.get_state())


class RecordChanges(Middleware[AppState, Actions], post_actions=Add):
    @intercept_post
    def add(self, action: Add, ctx: StoreAPI[AppState, Actions]) -> None:
        ctx.dispatch(Remember(ctx.get_state().count.value))


def main() -> None:
    store = Store[AppState, Actions](
        initial_state=AppState(), reducer=reduce,
        required_actions=Actions,
        middleware=[Log(), RecordChanges()],
    )
    store.dispatch(Add(3))
    assert store.get_state() == AppState(Count(3), History((3,)))
    old = store.get_state()
    store.dispatch(NoOp())
    assert store.get_state() is old
    store.dispatch(Reset())
    assert store.get_state() == AppState()


if __name__ == "__main__":
    main()
