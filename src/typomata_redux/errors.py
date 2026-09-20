"""Errors raised by declaration validation and dispatch infrastructure."""


class DefinitionError(TypeError):
    """A handler or composition is not a supported declaration."""


class AmbiguousHandlerError(ValueError):
    """Several handlers match; no handler has been invoked."""


class DispatchError(RuntimeError):
    """Dispatch or forwarding was attempted outside its allowed lifetime."""
