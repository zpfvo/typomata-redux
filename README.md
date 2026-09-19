# Typomata Redux

A first implementation of synchronous, typed Redux stores using
[Typomata](../typomata) state machines as reducers. Middleware handlers are selected
by action annotations. Side effects belong in middleware.

## Development

Requires Python 3.10+ and the reviewed Typomata checkout alongside this project:

```text
python/
  typomata/
  python-typomata-redux/
```

```bash
uv sync
uv run python -m unittest discover -s tests -v
uv run mypy
uv run pyright
uv run python examples/counter.py
uv build
uv run python scripts/verify_distribution.py
```

The uv source override installs the sibling Typomata checkout. Built distribution
metadata declares `typomata>=0.1.0`; compatibility with older published builds has
not been verified. Install this package together with the reviewed Typomata build
until that project's validated implementation is released.

## Reducers and middleware

```python
from dataclasses import dataclass
from typomata import BaseAction, BaseState, BaseStateMachine, transition
from typomata_redux import MachineReducer, Middleware, MiddlewareContext, Store, intercept

@dataclass(frozen=True)
class Count(BaseState):
    value: int = 0

@dataclass(frozen=True)
class Add(BaseAction):
    amount: int

class Counter(BaseStateMachine):
    @transition
    def add(self, state: Count, action: Add) -> Count:
        return Count(state.value + action.amount)

class Log(Middleware[Count, Add]):
    @intercept
    def add(self, action: Add, ctx: MiddlewareContext[Count, Add]) -> None:
        print("before", ctx.get_state())
        ctx.next(action)
        print("after", ctx.get_state())

store = Store[Count, Add](
    initial_state=Count(),
    reducer=MachineReducer[Count, Add](Counter(), states=Count, actions=Add),
    states=Count,
    actions=Add,
    middleware=[Log()],
)
store.dispatch(Add(2))  # Returns None.
assert store.get_state() == Count(2)
```

The explicit generic arguments describe static types. `states` and `actions`
are runtime schemas: pass subclasses of Typomata's bases, unions, or `Annotated`
forms. Keep those declarations aligned; generic arguments are not inspected at
runtime. Matching includes subclasses. A schema such as `BaseAction` is open to
all its subclasses. Dataclasses and nested values should be immutable; the
library validates types, not purity or deep immutability.

The runnable [example](examples/counter.py) uses a nested root state and an explicit
root reducer. A plain `(state, action) -> state` function is also accepted. Each
Typomata slice adapter returns the identical object for unmatched actions; root
composition should preserve root identity when every slice is unchanged.

## Dispatch contract

- `[First(), Second()]` runs First before Second, then reduces, commits state,
  notifies subscribers, and returns through Second and First.
- `ctx.next(action)` continues downstream. `ctx.dispatch(action)` starts a new
  dispatch through the entire chain. Both return `None`.
- No matching middleware method automatically forwards. A matching method may
  forward a replacement action or consume the action by not forwarding.
- An annotated handler's `next` is valid once, during that invocation. Additional
  or delayed actions use `dispatch`. No batching or async dispatch API is provided.
- Duplicate action registrations fail at class creation. Overlapping superclass
  or union handlers raise `AmbiguousHandlerError` at dispatch. No name-order or
  most-specific-match rule is applied. A broad logger belongs in its own middleware.
- Handlers are synchronous instance methods with three required positional
  parameters: receiver, action, and `MiddlewareContext[S, A]`; return annotation
  is `None`. Parameter names may differ; positional-only parameters are supported.
  Static/class methods, generators, async handlers, and unsupported annotations
  are rejected. Postponed annotations resolve in module/declaring-class scope;
  function-local forward references are not searched for automatically.
- Inherited handlers and decorated overrides work. An undecorated override removes
  the inherited registration. Direct calls preserve their static signatures and
  validate action/result types; `super()` calls to registered parent methods work.
- A reducer exception or invalid result leaves the previous state installed.
  Errors after commit cannot roll state back. Middleware, reducer, and listener
  exceptions propagate unchanged. A listener exception stops that notification pass.
- `subscribe(listener)` returns an idempotent unsubscribe callback. Each successful
  reducer dispatch notifies a snapshot of listeners, including no-ops. Each separate
  registration is independent. Consumed actions do not notify on their own.
- Nested dispatch is synchronous. Subscribers may see newer state after another
  subscriber dispatches. Reducers cannot call back into their store. Dispatch during
  middleware construction is rejected.
- Store access belongs to its creating thread. Middleware can start async work
  using application-owned tasks or workers, then dispatch completion actions on
  that thread. Cancellation, shutdown, stale responses, and errors belong to that
  middleware. The library starts no threads or event loops.

Middleware context is per invocation and store bindings are per store. Reusing a
middleware object does not overwrite its binding, but any mutable fields added to
that object remain shared: use separate instances for independently owned effects.

## Plain middleware

For custom composition, a factory receives `(api, next_dispatch)` and returns a
synchronous action handler. This two-argument form avoids nested factory closures:

```python
from typomata_redux import Dispatch, StoreAPI

def logging(api: StoreAPI[Count, Add], next_dispatch: Dispatch[Add]) -> Dispatch[Add]:
    def handle(action: Add) -> None:
        print(api.get_state())
        next_dispatch(action)
    return handle
```

Factories run right-to-left as the chain is assembled. Execution runs in configured
order. Store boundaries validate action types and `None` results for both forms.
The per-invocation one-call/lifetime guard is supplied by annotated `Middleware`;
plain factories manage their own forwarding lifetimes.

## Scope

This implementation adapts public `transition_map()` snapshots and invokes
Typomata's decorated methods. It does not use private Typomata internals or modify
Typomata. There are no diagrams yet; static action-handler diagrams are the final
planned feature. See [plan.md](plan.md) for design decisions and follow-up work.
