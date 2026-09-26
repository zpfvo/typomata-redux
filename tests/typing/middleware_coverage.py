"""Phase declarations preserve signatures; assert_never still checks bodies."""
from typing import TypeAlias

from typing_extensions import assert_never, assert_type
from typomata_redux import Middleware, Store, StoreAPI, intercept_post, intercept_pre
from consumer import Actions, Add, Count, Ignore, plain_counter

PreActions: TypeAlias = Add | Ignore
PostActions: TypeAlias = Add | Ignore
API: TypeAlias = StoreAPI[Count, Actions]


class Covered(Middleware[Count, Actions], pre_actions=PreActions, post_actions=PostActions):
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    @intercept_pre
    def add(self, event: Add, context: API) -> None:
        print(self.prefix, event.amount, context.get_state().value)

    @intercept_pre()
    def ignore(self, event: Ignore, context: API) -> None:
        assert_type(context.get_state(), Count)

    @intercept_post(catch_exceptions=True)
    def after(self, event: PostActions, context: API) -> None:
        if isinstance(event, Add):
            print(event.amount)
            return
        if isinstance(event, Ignore):
            return
        assert_never(event)


class Inherited(Covered):
    @intercept_pre
    def add(self, event: Add, context: API) -> None:
        super().add(event=event, context=context)


class PostOnly(Covered, pre_actions=None):
    def add(self, event: Add, context: API) -> None:
        pass

    def ignore(self, event: Ignore, context: API) -> None:
        pass


def check(context: API) -> None:
    assert_type(Inherited('count').add(event=Add(), context=context), None)
    assert_type(PostOnly('count').after(event=Ignore(), context=context), None)
    Store[Count, Actions](initial_state=Count(), reducer=plain_counter,
                          middleware=[Covered('count'), Inherited('count'), PostOnly('count')])


class Incomplete(Middleware[Count, Actions], pre_actions=PreActions, post_actions=PostActions):
    # Registration covers both union members. Static checking separately catches
    # the missing branch inside each handler; coverage cannot inspect method bodies.
    @intercept_pre
    def before(self, event: PreActions, context: API) -> None:
        if isinstance(event, Add):
            return
        assert_never(event)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]

    @intercept_post
    def after(self, event: PostActions, context: API) -> None:
        if isinstance(event, Ignore):
            return
        assert_never(event)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
