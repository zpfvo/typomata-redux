"""Checked as an installed consumer, including intentionally invalid calls."""
from dataclasses import dataclass

from typing_extensions import assert_type
from typomata import BaseAction, BaseState, BaseStateMachine, transition
from typomata_redux import CombinedReducer, Dispatch, MachineReducer, Middleware, MiddlewareContext, Store, StoreAPI, combine_reducers, intercept, intercept_pre, intercept_post


@dataclass(frozen=True)
class Count(BaseState):
    value: int = 0


@dataclass(frozen=True)
class Root(BaseState):
    count: Count = Count()


@dataclass(frozen=True)
class Nested(BaseState):
    feature: Root = Root()


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
    reducer = MachineReducer[Count](Counter())
    combined = combine_reducers(Root, count=reducer)
    assert_type(combined, CombinedReducer[Root])
    assert_type(combined(Root(), Add()), Root)
    nested = combine_reducers(Nested, feature=combined)
    assert_type(nested, CombinedReducer[Nested])
    assert_type(nested(Nested(), Ignore()), Nested)
    assert_type(combined(Root(), Foreign()), Root)  # Broad slice dispatch is intentional.
    combined(Count(), Add())  # type: ignore[arg-type]
    CombinedReducer[Root](Root, count=reducer)
    store = Store[Count, Actions](
        initial_state=Count(), reducer=reducer,
        middleware=[Logging(), passthrough],
    )
    assert_type(reducer(Count(), Add()), Count)
    assert_type(Counter().add(state=Count(), action=Add()), Count)
    assert_type(store.get_state(), Count)
    store.dispatch(Add())
    store.dispatch(Ignore())
    store.dispatch(Foreign())  # type: ignore[arg-type]
    assert_type(reducer(Count(), Foreign()), Count)
    reducer(Root(), Add())  # type: ignore[arg-type]
    Counter().add(Count(), Foreign())  # type: ignore[arg-type]
    reducer(Count(), object())  # type: ignore[arg-type]
    store.subscribe(lambda state: None)  # type: ignore[misc, arg-type]
    context = MiddlewareContext(store.get_state, store.dispatch, store.dispatch)
    Logging().log(event=Add(), context=context)
    Logging().log(event=Ignore(), context=context)  # type: ignore[arg-type]
    Logging().log(action=Add(), context=context)  # type: ignore[call-arg]


class Automatic(Middleware[Count, Actions]):
    @intercept_pre
    def before(self, action: Add, ctx: StoreAPI[Count, Actions]) -> None:
        assert_type(ctx.get_state(), Count)
        ctx.next(action)  # type: ignore[attr-defined]
        ctx.dispatch(Foreign())  # type: ignore[arg-type]

    @intercept_post
    def after(self, action: Add, ctx: StoreAPI[Count, Actions]) -> None:
        ctx.dispatch(Ignore())


def check_automatic(api: StoreAPI[Count, Actions]) -> None:
    Automatic().before(action=Add(), ctx=api)
    Automatic().after(action=Add(), ctx=api)
    Automatic().before(action=Ignore(), ctx=api)  # type: ignore[arg-type]
    Automatic().after(action=Ignore(), ctx=api)  # type: ignore[arg-type]


# A plain slice reducer must accept BaseAction; its body narrows before reading payloads.
def plain_counter(state: Count, action: BaseAction) -> Count:
    if isinstance(action, Add):
        return Count(state.value + action.amount)
    return state


def narrow_counter(state: Count, action: Add) -> Count:
    return Count(state.value + action.amount)


def check_store_contract() -> None:
    root = combine_reducers(Root, count=plain_counter)
    store = Store[Root, Actions](initial_state=Root(), reducer=root)
    assert_type(store.get_state(), Root)
    store.dispatch(Foreign())  # type: ignore[arg-type]
    store.dispatch(object())  # type: ignore[arg-type]
    CombinedReducer[Root](Root, count=narrow_counter)  # type: ignore[arg-type]
    Store[Root, Actions](initial_state=Count(), reducer=root)  # type: ignore[arg-type]
    Store[Root, Actions](initial_state=Root(), reducer=plain_counter)  # type: ignore[arg-type]
