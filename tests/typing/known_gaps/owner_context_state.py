"""Desired rejection: handler context state contradicts its owner. Review item 2."""
from typomata_redux import Middleware, Store, StoreAPI, intercept_pre
from consumer import Actions, Add, Count, Root, plain_counter


class WrongContext(Middleware[Count, Actions]):
    @intercept_pre
    def before(self, action: Add, ctx: StoreAPI[Root, Actions]) -> None:
        print(ctx.get_state().count)


store = Store[Count, Actions](initial_state=Count(), reducer=plain_counter, middleware=[WrongContext()])
