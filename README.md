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
    reducer=MachineReducer[Count](Counter()),
    middleware=[Log()],
)
store.dispatch(Add(2))  # Returns None.
assert store.get_state() == Count(2)
```

Types are declared through generics and handler annotations, with no separate
`states=` or `actions=` configuration. `MachineReducer[Count]` returns `Count`;
`Store[Count, Add]` exposes `get_state() -> Count` and `dispatch(action: Add) -> None`.
State unions work too: `MachineReducer[Idle | Loading | Ready](DownloadMachine())`.

Slice dispatch accepts `BaseAction`, calls a matching transition, or returns the
identical state object. Each transition method keeps its precise state/action
annotations, so slices need not import the application's full action union.
Declare the allowed application actions on the store, for example
`Store[AppState, Increment | Reset](...)`. Those generic arguments are static
contracts; the library does not introspect them to enforce the unions at runtime.

Runtime checks remain where existing information is sufficient: transition
annotations, action/state base classes, dataclass field types, ambiguity, and
middleware forwarding rules. Passing an unrelated `BaseAction` through untyped
code is not rejected merely because it is outside the store's static action union.
A plain reducer's result is checked as `BaseState`, not against the store's generic
state union. Frozen dataclasses and immutable nested values are recommended;
checks do not prove purity or deep immutability.

The runnable [example](examples/counter.py) uses a nested root state and composed
slice reducers. A plain `(state, action) -> state` function is also accepted.

## Combining reducers

Use the root dataclass's field names to wire existing slice reducers:

```python
from typomata_redux import combine_reducers

reducer = combine_reducers(
    AppState,
    count=counter,
    history=recorder,
)
```

Here `counter` and `recorder` are the `MachineReducer` instances from the runnable
example. Pass `reducer` directly to `Store`; no handwritten root reducer is needed.
Each action goes to both children, with their own original slice state. The helper
returns the identical root object if both slices are unchanged; otherwise it
rebuilds the dataclass once, sharing unchanged branches. Unconfigured constructor
fields keep their values. Comparison uses identity, not equality.

Composition nests to match your application's state shape:

```python
downloads = combine_reducers(DownloadsState, current=download_reducer)
reducer = combine_reducers(AppState, downloads=downloads, settings=settings_reducer)
```

Each configured field must be annotated with a `BaseState` subclass, a union of
state classes, or an `Annotated` form. Other fields, such as labels or configuration,
can be left unconfigured. Children may be machine adapters, combined reducers,
or ordinary synchronous functions. Plain slice functions must accept `BaseAction`
and narrow it before reading action-specific fields:

```python
def count_reducer(state: Count, action: BaseAction) -> Count:
    if isinstance(action, Add):
        return Count(state.value + action.amount)
    return state
```

Machine adapters do this dispatch automatically using transition annotations.
Initial values still come from `Store(initial_state=...)`. A plain root reducer
passed directly to `Store` can instead accept the store's specific action union.

The root state type is inferred and preserved statically. Composed reducers accept
`BaseAction`; the application action union belongs on the store. Python type checkers cannot
verify keyword names against dataclass field types: constructor checks reject
unknown fields and incompatible transition or nested-composition declarations; runtime checks
validate each field input and result. A plain function's state annotation is not
inspected; annotate it normally for static checking. Miswiring one can fail inside
that function. Different field types require a deliberately erased state type at
this composition boundary; downstream root-state access remains precisely typed.

For explicit type parameters, use
`CombinedReducer[AppState](AppState, count=counter, history=recorder)`.
`combine_reducers` infers the state type from the root class. An empty
`CombinedReducer[AppState](AppState)` is an identity reducer.

Rebuilding uses `dataclasses.replace`, including normal constructor/`__post_init__`
behavior. Fields with `init=False` cannot be targeted and may be recomputed when
the root is rebuilt. Required `InitVar` arguments need a custom root reducer.
Reducers must remain pure; if a later child fails, the store does not commit the
partially computed root, but it cannot undo in-place mutation or side effects.

## Static typing boundaries

- Store dispatch, middleware context dispatch, state access, and direct handler
  calls retain precise static types. Run mypy or Pyright on application code.
- `MachineReducer[S]` is a declaration by the caller: Typomata's `BaseStateMachine`
  is not generic, so a checker cannot prove that all registered transitions stay
  inside `S`. Use the union of possible slice states. Typomata checks each actual
  transition result against its return annotation; composition additionally checks
  declarations and results against the dataclass field type.
- Composition field names and heterogeneous reducer wiring remain runtime-checked.
  This simplification removes duplicate declarations, not these static limitations.
- Middleware handler action annotations determine routing. Their relation to the
  middleware/store's generic action union is not exhaustively checked at registration.

## Migrating the earlier API

- `MachineReducer[S, A](machine, states=S, actions=A)` becomes `MachineReducer[S](machine)`.
- `CombinedReducer[S, A](S, ...)` becomes `CombinedReducer[S](S, ...)`, or use `combine_reducers(S, ...)`.
- `Store[S, A](..., states=S, actions=A)` becomes `Store[S, A](...)`.
- Remove narrow-action routing adapters. Slice dispatch accepts `BaseAction` and
  ignores unmatched actions; handler signatures remain narrow.

The old schema keywords are removed, not silently accepted. Initial state still
comes from `initial_state`. Runtime enforcement of the store's exact generic
state/action vocabulary is no longer provided.

## Dispatch contract

- `[First(), Second()]` runs First before Second, then reduces, commits state,
  notifies subscribers, and returns through Second and First.
- `ctx.next(action)` continues downstream. `ctx.dispatch(action)` starts a new
  dispatch through the entire chain. Both return `None`.
- No matching middleware method automatically forwards. A matching method may
  forward a replacement action or consume the action by not forwarding.
- An annotated handler's `next` is valid once, during that invocation. Additional
  or delayed actions use `dispatch`. No batching or async dispatch API is provided.
- Duplicate action registrations within one phase, or manual/automatic conflicts,
  fail at class creation. Overlapping superclass
  or union handlers raise `AmbiguousHandlerError` at dispatch. No name-order or
  most-specific-match rule is applied. A broad logger belongs in its own middleware.
- Handlers are synchronous instance methods with three required positional
  parameters: receiver, action, and context (`MiddlewareContext[S, A]` for manual
  handlers, `StoreAPI[S, A]` for automatic pre/post handlers); return annotation
  is `None`. Parameter names may differ; positional-only parameters are supported.
  Static/class methods, generators, async handlers, and unsupported annotations
  are rejected. Postponed annotations resolve in module/declaring-class scope;
  function-local forward references are not searched for automatically.
- Inherited handlers and decorated overrides work. An undecorated override removes
  the inherited registration. Direct calls preserve their static signatures and
  validate action/result types; `super()` calls to registered parent methods work.
- A reducer exception or a result rejected by the remaining runtime checks leaves
  the previous state installed.
  Errors after commit cannot roll state back. Middleware, reducer, and listener
  exceptions propagate unchanged by default; annotated handlers can opt into recovery
  as described below. A listener exception stops that notification pass.
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

## Automatic pre/post handlers

Use `@intercept_pre` and `@intercept_post` when forwarding should be automatic:

```python
from typomata_redux import StoreAPI, intercept_pre, intercept_post

class Log(Middleware[Count, Add]):
    @intercept_pre
    def before(self, action: Add, ctx: StoreAPI[Count, Add]) -> None:
        print("before", ctx.get_state())

    @intercept_post
    def after(self, action: Add, ctx: StoreAPI[Count, Add]) -> None:
        print("after", ctx.get_state())
```

`StoreAPI` provides `get_state` and full-chain `dispatch`, with no `next`.
Pre runs before forwarding; post runs after downstream returns successfully.
One matching pre and one matching post may coexist in the same middleware:
`pre → downstream → post`. Either phase may be used alone. Across middleware
instances, pre runs in configured order and post unwinds in reverse order.

**Post means after downstream, not necessarily after reduction.** It runs even
when a downstream handler consumes the action, and receives the original action
seen by this middleware even if downstream replaces it. `get_state()` reads the
current state, including changes from nested dispatches. A downstream exception
(including a subscriber error after commit) skips post. A pre exception stops
forwarding by default; a post exception propagates without rolling back committed state.
These hooks are not `finally` handlers.

Keep `@intercept` with `MiddlewareContext` when you need manual forwarding,
replacement, or consumption. A matching manual handler cannot coexist with a
matching pre/post handler in the same middleware. Duplicates within a phase also
conflict. Exact action conflicts fail during class creation; broader overlapping
matches fail at dispatch **before any of that middleware's handlers run**.
Use one decorator per method. Direct calls validate and invoke only that method;
automatic forwarding belongs to the middleware chain, not the decorator wrapper.

## Passing dependencies to middleware

Pass database connections, API clients, or other services through the middleware's
constructor. Store them as typed instance attributes and use them in handlers.
The context supplies store access; dependencies belong to the middleware instance.

Building on the counter example above, this middleware accepts a SQLite connection:

```python
import sqlite3
from typomata_redux import StoreAPI, intercept_post

class SaveCount(Middleware[Count, Add]):
    def __init__(self, database: sqlite3.Connection) -> None:
        self.database = database

    @intercept_post
    def save(self, action: Add, ctx: StoreAPI[Count, Add]) -> None:
        with self.database:
            self.database.execute(
                "INSERT INTO counts (value) VALUES (?)",
                (ctx.get_state().value,),
            )

database = sqlite3.connect(":memory:")
try:
    database.execute("CREATE TABLE counts (value INTEGER NOT NULL)")
    store = Store[Count, Add](
        initial_state=Count(),
        reducer=MachineReducer[Count](Counter()),
        middleware=[SaveCount(database)],
    )
    store.dispatch(Add(2))
    assert database.execute("SELECT value FROM counts").fetchall() == [(2,)]
finally:
    database.close()
```

The application creates and closes the connection; the store does not manage its
lifetime. Multiple middleware instances can receive the same dependency. For tests,
inject a test database, or annotate a service dependency with a `Protocol` and
provide a fake implementation. Constructor injection preserves static typing
without adding another generic parameter to the middleware context.

This post-handler records the state observed after downstream returns, with the
pre/post semantics described above. A database failure propagates by default;
the store's already committed state is not rolled back.

## Middleware exception handling

All three decorators accept `catch_exceptions=True`; the default is `False`.
Enable it for effects whose failure should allow dispatch to continue:

```python
from typomata_redux import CancelAction, MiddlewareError, intercept_pre

class Audit(Middleware[Count, Add]):
    @intercept_pre(catch_exceptions=True)
    def before(self, action: Add, ctx: StoreAPI[Count, Add]) -> None:
        print("dispatching", action)  # An ordinary effect failure is logged and recovered.
```

Recovery logs the exception with its traceback to `typomata_redux.middleware`.
It ends the failing handler; it does not resume its remaining statements.

| Handler | After recovering its own exception |
| --- | --- |
| `intercept_pre` | Forward normally, then run its post-handler if downstream succeeds. |
| `intercept_post` | Return normally, allowing earlier middleware to unwind. |
| `intercept` before calling `next` | Forward the original action once. |
| `intercept` after calling `next` successfully | Return without forwarding again. |

Returning normally from a manual handler without calling `next` still consumes
an action, even with recovery enabled.

**`raise CancelAction()` consumes the action and unwinds normally**, regardless
of `catch_exceptions`. Earlier middleware's post-handlers still run. Cancellation
in a pre-handler skips its own post-handler. Cancellation after `next` or in a
post-handler cannot undo reduction or side effects that already happened.

**`raise MiddlewareError("reason")` always propagates**, skips remaining
post-handlers, and does not roll back committed state. Use `raise
MiddlewareError("reason") from error` to preserve the underlying cause.

Recovery covers ordinary `Exception` failures from the handler itself. Failures
from `ctx.next`, `ctx.dispatch`, or `ctx.get_state` propagate, including reducer,
subscriber, and nested-dispatch failures. Once a context call fails, any later
exception in that invocation also propagates, even if the handler catches and
translates the original error. Explicitly catching an error and returning normally
remains possible. Use the supplied context for store calls so this boundary can
be tracked.

Library definition, dispatch, ambiguity, and return-contract errors are not
recovered. `KeyboardInterrupt`, `SystemExit`, and other `BaseException` subclasses
are not recovered either. Direct method calls (including `super()`) use ordinary
Python exception behavior; recovery and cancellation handling belong to the chain.
Plain middleware factories retain their own exception policy.

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
order. Store boundaries check that actions inherit `BaseAction` and handlers return
`None`; they do not runtime-enforce the store's generic action union.
The per-invocation one-call/lifetime guard is supplied by annotated `Middleware`;
plain factories manage their own forwarding lifetimes.

## Scope

This implementation adapts public `transition_map()` snapshots and invokes
Typomata's decorated methods. It does not use private Typomata internals or modify
Typomata. There are no diagrams yet; static action-handler diagrams are the final
planned feature.
