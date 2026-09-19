"""Checked as an installed consumer, including intentionally invalid calls."""
from dataclasses import dataclass

from typing_extensions import assert_type
from typomata import BaseAction, BaseState, BaseStateMachine, transition
from typomata_redux import Dispatch, MachineReducer, Middleware, MiddlewareContext, Store, StoreAPI, intercept


@dataclass(frozen=True)
class Count(BaseState):
    value: int = 0


@dataclass(frozen=True)
class Add(BaseAction):
    amount: int = 1


class Ignore(BaseAction):
    pass


class Foreign(BaseAction):
    pass


Actions = Add | Ignore


class Counter(BaseStateMachine):
    @transition
    def add(self, state: Count, action: Add) -> Count:
        return Count(state.value + action.amount)


class Logging(Middleware[Count, Actions]):
    @intercept
    def log(self, event: Add, context: MiddlewareContext[Count, Actions]) -> None:
        assert_type(event, Add)
        assert_type(context.get_state(), Count)
        context.next(event)
        context.dispatch(Ignore())
        context.next(Foreign())  # type: ignore[arg-type]
        context.dispatch(Foreign())  # type: ignore[arg-type]


def passthrough(api: StoreAPI[Count, Actions], next_dispatch: Dispatch[Actions]) -> Dispatch[Actions]:
    assert_type(api.get_state(), Count)
    return next_dispatch


def check() -> None:
    reducer = MachineReducer[Count, Actions](Counter(), states=Count, actions=Actions)
    store = Store[Count, Actions](
        initial_state=Count(), reducer=reducer, states=Count, actions=Actions,
        middleware=[Logging(), passthrough],
    )
    assert_type(reducer(Count(), Add()), Count)
    assert_type(Counter().add(state=Count(), action=Add()), Count)
    assert_type(store.get_state(), Count)
    store.dispatch(Add())
    store.dispatch(Ignore())
    store.dispatch(Foreign())  # type: ignore[arg-type]
    reducer(Count(), Foreign())  # type: ignore[arg-type]
    store.subscribe(lambda state: None)  # type: ignore[misc, arg-type]
    context = MiddlewareContext(store.get_state, store.dispatch, store.dispatch)
    Logging().log(event=Add(), context=context)
    Logging().log(event=Ignore(), context=context)  # type: ignore[arg-type]
    Logging().log(action=Add(), context=context)  # type: ignore[call-arg]
