"""Desired rejection: routed handler action is outside the owner's vocabulary."""
from typomata_redux import Middleware, Store, StoreAPI, intercept_pre
from consumer import Actions, Count, Foreign, plain_counter


class WrongAction(Middleware[Count, Actions], pre_actions=Foreign):
    @intercept_pre
    def before(self, action: Foreign, ctx: StoreAPI[Count, Actions]) -> None:
        pass


store = Store[Count, Actions](initial_state=Count(), reducer=plain_counter, middleware=[WrongAction()])
