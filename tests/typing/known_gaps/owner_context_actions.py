"""Desired rejection: handler context dispatch vocabulary contradicts its owner."""
from typomata_redux import Middleware, Store, StoreAPI, intercept_pre
from consumer import Actions, Add, Count, Foreign, plain_counter


class WrongContext(Middleware[Count, Actions]):
    @intercept_pre
    def before(self, action: Add, ctx: StoreAPI[Count, Foreign]) -> None:
        ctx.dispatch(Foreign())


store = Store[Count, Actions](initial_state=Count(), reducer=plain_counter, middleware=[WrongContext()])
