"""Errors raised by declaration validation and dispatch infrastructure."""


class DefinitionError(TypeError):
    """A handler or composition is not a supported declaration."""


class AmbiguousHandlerError(ValueError):
    """Several handlers match; no handler has been invoked."""


class DispatchError(RuntimeError):
    """Dispatch or forwarding was attempted outside its allowed lifetime."""


class CancelAction(Exception):
    """Consume the current action and unwind normally to earlier middleware.

    Raise inside an annotated middleware handler to skip any remaining forwarding
    by that middleware. Earlier post-handlers still run. If next has already run,
    this only ends the current handler: it cannot roll back state or side effects.
    Cancellation in a pre-handler also skips that middleware's own post-handler.
    The signal is handled by the middleware chain, not by direct method calls.
    """


class MiddlewareError(RuntimeError):
    """Abort dispatch even when a handler enables catch_exceptions.

    Propagates to the caller and skips remaining post-handlers. State or effects
    already committed are not rolled back. Use 'raise MiddlewareError(...) from
    error' to retain the underlying failure.
    """
