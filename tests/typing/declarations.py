"""Invalid declarations and wiring that the current API can reject statically."""
from typomata_redux import Middleware, MiddlewareContext, Store, StoreAPI, intercept, intercept_pre, intercept_post
from consumer import Actions, Add, Count, Foreign, Logging, Root, plain_counter


class BadState(Middleware[int, Actions]):  # type: ignore[type-var]  # pyright: ignore[reportInvalidTypeArguments]
    pass


class BadAction(Middleware[Count, str]):  # type: ignore[type-var]  # pyright: ignore[reportInvalidTypeArguments]
    pass


class BadReturns(Middleware[Count, Actions]):
    @intercept  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType, reportCallIssue]
    def manual(self, action: Add, ctx: MiddlewareContext[Count, Actions]) -> int:
        return 1

    @intercept_pre(catch_exceptions=True)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    def before(self, action: Add, ctx: StoreAPI[Count, Actions]) -> int:
        return 1

    @intercept_post()  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    async def after(self, action: Add, ctx: StoreAPI[Count, Actions]) -> None:
        pass


class BadConfiguration(Middleware[Count, Actions]):
    @intercept_pre(catch_exceptions='yes')  # type: ignore[call-overload, untyped-decorator]  # pyright: ignore[reportArgumentType]
    def before(self, action: Add, ctx: StoreAPI[Count, Actions]) -> None:
        pass


def check_context_and_store_wiring(
    other_state: MiddlewareContext[Root, Actions],
    other_actions: MiddlewareContext[Count, Foreign],
    other_middleware: Middleware[Root, Actions],
    narrow_middleware: Middleware[Count, Add],
) -> None:
    Logging().log(event=Add(), context=other_state)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    Logging().log(event=Add(), context=other_actions)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    Store[Count, Actions](initial_state=Count(), reducer=plain_counter, middleware=[other_middleware])  # type: ignore[list-item]  # pyright: ignore[reportArgumentType]
    Store[Count, Actions](initial_state=Count(), reducer=plain_counter, middleware=[narrow_middleware])  # type: ignore[list-item]  # pyright: ignore[reportArgumentType]
