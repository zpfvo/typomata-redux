"""Generic middleware keeps precise contexts through concrete specialization."""
from typing import TypeVar

from typing_extensions import assert_type
from typomata_redux import Middleware, Store, StoreAPI, intercept_pre
from consumer import Actions, Add, Count, plain_counter

S = TypeVar('S')


class GenericEffects(Middleware[S, Actions], pre_actions=Add):
    @intercept_pre
    def before(self, action: Add, ctx: StoreAPI[S, Actions]) -> None:
        assert_type(ctx.get_state(), S)
        ctx.dispatch(action)


class Counts(GenericEffects[Count]):
    pass


def check(ctx: StoreAPI[Count, Actions]) -> None:
    assert_type(Counts().before(action=Add(), ctx=ctx), None)
    Store[Count, Actions](initial_state=Count(), reducer=plain_counter, middleware=[Counts()])
