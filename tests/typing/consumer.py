"""Positive consumer examples plus explicit negative diagnostic expectations.

verify_typing.py removes both checkers' suppressions in a temporary copy and
verifies the diagnostic codes independently. This module is never executed.
"""
from dataclasses import dataclass

from typing_extensions import assert_type
from typomata_redux import CombinedReducer, Dispatch, FunctionReducer, Middleware, MiddlewareContext, Store, StoreAPI, combine_reducers, intercept, intercept_pre, intercept_post


@dataclass(frozen=True)
class Count:
    value: int = 0


@dataclass(frozen=True)
class Root:
    count: Count = Count()


@dataclass(frozen=True)
class Nested:
    feature: Root = Root()


@dataclass(frozen=True)
class Add:
    amount: int = 1


class Ignore:
    pass


class Foreign:
    pass


Actions = Add | Ignore


class Counter:
    def add(self, state: Count, action: Add) -> Count:
        return Count(state.value + action.amount)


class Logging(Middleware[Count, Actions], manual_actions=Add):
    @intercept(catch_exceptions=True)
    def log(self, event: Add, context: MiddlewareContext[Count, Actions]) -> None:
        assert_type(event, Add)
        assert_type(context.get_state(), Count)
        context.next(event)
        context.dispatch(Ignore())
        context.next(Foreign())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
        context.dispatch(Foreign())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


def passthrough(api: StoreAPI[Count, Actions], next_dispatch: Dispatch[Actions]) -> Dispatch[Actions]:
    assert_type(api.get_state(), Count)
    return next_dispatch


def check() -> None:
    reducer = FunctionReducer(Counter().add)
    combined = combine_reducers(Root, count=reducer)
    assert_type(combined, CombinedReducer[Root])
    assert_type(combined(Root(), Add()), Root)
    nested = combine_reducers(Nested, feature=combined)
    assert_type(nested, CombinedReducer[Nested])
    assert_type(nested(Nested(), Ignore()), Nested)
    assert_type(combined(Root(), Foreign()), Root)  # Broad slice dispatch is intentional.
    combined(Count(), Add())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
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
    store.dispatch(Foreign())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    assert_type(reducer(Count(), Foreign()), Count)
    reducer(Root(), Add())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    Counter().add(Count(), Foreign())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    reducer(Count(), object())
    store.subscribe(lambda state: None)  # type: ignore[misc, arg-type]  # pyright: ignore[reportArgumentType]
    context = MiddlewareContext(store.get_state, store.dispatch, store.dispatch)
    Logging().log(event=Add(), context=context)
    Logging().log(event=Ignore(), context=context)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    Logging().log(action=Add(), context=context)  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]


class Automatic(Middleware[Count, Actions], pre_actions=Add, post_actions=Add):
    @intercept_pre(catch_exceptions=True)
    def before(self, action: Add, ctx: StoreAPI[Count, Actions]) -> None:
        assert_type(ctx.get_state(), Count)
        ctx.next(action)  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]
        ctx.dispatch(Foreign())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]

    @intercept_post()
    def after(self, action: Add, ctx: StoreAPI[Count, Actions]) -> None:
        ctx.dispatch(Ignore())


def check_automatic(api: StoreAPI[Count, Actions]) -> None:
    Automatic().before(action=Add(), ctx=api)
    Automatic().after(action=Add(), ctx=api)
    Automatic().before(action=Ignore(), ctx=api)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    Automatic().after(action=Ignore(), ctx=api)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


# A plain slice reducer must accept object; its body narrows before reading payloads.
def plain_counter(state: Count, action: object) -> Count:
    if isinstance(action, Add):
        return Count(state.value + action.amount)
    return state


def narrow_counter(state: Count, action: Add) -> Count:
    return Count(state.value + action.amount)


def check_store_contract() -> None:
    root = combine_reducers(Root, count=plain_counter)
    store = Store[Root, Actions](initial_state=Root(), reducer=root)
    assert_type(store.get_state(), Root)
    store.dispatch(Foreign())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    store.dispatch(object())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    CombinedReducer[Root](Root, count=narrow_counter)
    Store[Root, Actions](initial_state=Count(), reducer=root)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    Store[Root, Actions](initial_state=Root(), reducer=plain_counter)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


# Decorator typing must keep subclass dependencies, inheritance, narrow action
# handlers, and keyword parameter names usable while preserving return types.
class Audit(Middleware[Count, Actions], pre_actions=Add):
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    @intercept_pre
    def before(self, event: Add, context: StoreAPI[Count, Actions]) -> None:
        print(self.prefix, context.get_state().value)


class DetailedAudit(Audit):
    @intercept_pre(catch_exceptions=True)
    def before(self, event: Add, context: StoreAPI[Count, Actions]) -> None:
        super().before(event=event, context=context)
        context.dispatch(Ignore())


def check_subclass(api: StoreAPI[Count, Actions]) -> None:
    assert_type(DetailedAudit("count").before(event=Add(), context=api), None)
    Store[Count, Actions](
        initial_state=Count(), reducer=plain_counter,
        middleware=[DetailedAudit("count")],
    )
