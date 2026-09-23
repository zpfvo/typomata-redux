# Typomata Redux

Synchronous, typed Redux stores with [Typomata](../typomata) state machines as
reducers. Describe state changes with annotated methods, compose them over nested
state, and keep side effects in middleware.

This is an early implementation. The core API is ready to try in an application;
compatibility with published Typomata releases still needs verification.

- [Setup](#setup) and [quick start](#quick-start)
- [Combining reducers](#combining-reducers)
- [Choosing a middleware decorator](#choosing-a-middleware-decorator)
- [Passing dependencies](#passing-dependencies-to-middleware)
- [Exception handling](#middleware-exception-handling)
- [Subscriptions](#subscriptions) and [async work](#async-work)
- [API reference](#api-reference), [typing limits](#static-typing-boundaries), and [development](#development)

## How the pieces fit

An **action** is a `BaseAction` object describing what happened. **State** is a
`BaseState` object; use immutable values so old state remains meaningful.
A **reducer** takes state and an action and returns the next state without side
effects. A **store** owns the current state and sends dispatched actions through
its middleware to the reducer.

Typomata selects reducer methods by **state type and action type**. Middleware
selects methods by **action type only**; a handler reads any needed state through
its context. An unhandled reducer action returns the same state object. An
unhandled middleware action continues downstream.

With `middleware=[First(), Second()]`, normal dispatch proceeds in this order:

```text
First pre → Second pre → reducer → commit state → subscribers
                                                ↓
First post ← Second post ←───────────────────────┘
```

Manual middleware controls forwarding explicitly. Consumption and exceptions can
shorten this sequence; the sections below explain how post-handlers unwind.

## Setup

Requires Python 3.10+ and the reviewed Typomata checkout alongside this project:

```text
python/
  typomata/
  python-typomata-redux/
```

```bash
uv sync
uv run python examples/counter.py
```

The uv source override installs the sibling Typomata checkout. Built distribution
metadata declares `typomata>=0.1.0`; compatibility with older published builds has
not been verified. Install this package together with the reviewed Typomata build
until that project's validated implementation is released.

## Quick start

This complete example defines an action, state, reducer, and manual middleware:

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

The store's `dispatch` returns `None`; read the current state with `get_state()`.
`ctx.next(action)` forwards to the next middleware or the reducer. Returning without
calling it consumes the action, then earlier middleware resumes normally.

Use `Store[State, ActionUnion]` for the application's action vocabulary. Individual
handler annotations can stay narrow. State unions work too, for example
`MachineReducer[Idle | Loading | Ready](DownloadMachine())`.

The runnable [example](examples/counter.py) uses a nested root state and composed
slice reducers. A plain `(state, action) -> state` function is also accepted.

## Combining reducers

Use the root dataclass's field names to wire slice reducers. This extends the quick start with a root state containing a counter slice:

```python
from typomata_redux import combine_reducers

@dataclass(frozen=True)
class AppState(BaseState):
    count: Count = Count()
    title: str = "Counter"

reducer = combine_reducers(
    AppState,
    count=MachineReducer[Count](Counter()),
)
app = Store[AppState, Add](initial_state=AppState(), reducer=reducer)
app.dispatch(Add(3))
assert app.get_state().count == Count(3)
assert app.get_state().title == "Counter"
```

Pass the combined reducer directly to `Store`; no handwritten root reducer is
needed. The [runnable example](examples/counter.py) composes two slices.
Each action goes to every configured child, with their own original slice state. The helper
returns the identical root object if all slices are unchanged; otherwise it
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

## Choosing a middleware decorator

| Decorator | Context | Forwarding | Typical use |
| --- | --- | --- | --- |
| `@intercept_pre` | `StoreAPI[S, A]` | Automatic after the handler | Run an effect before downstream handling. |
| `@intercept_post` | `StoreAPI[S, A]` | Downstream runs first | React to an action using the resulting observable state. |
| `@intercept` | `MiddlewareContext[S, A]` | Explicit `ctx.next(action)` | Consume or replace actions, or compare state around forwarding. |

All contexts expose `get_state()` and `dispatch(action)`. Only manual handlers
receive `next(action)`. Both dispatch methods return `None`:

- `next` continues through the remaining middleware and can be called once during
  the current handler invocation.
- `dispatch` starts a new synchronous dispatch through the whole chain, including
  the current middleware. Dispatching the same action unconditionally can recurse.

To compare state before and after downstream handling:

```python
class ObserveCount(Middleware[Count, Add]):
    @intercept
    def observe(self, action: Add, ctx: MiddlewareContext[Count, Add]) -> None:
        previous = ctx.get_state()
        ctx.next(action)
        current = ctx.get_state()
        if current is not previous:
            print(previous.value, "->", current.value)
```

These are references to immutable state objects, not copies. The comparison
includes any changes from downstream nested dispatches; it does not isolate one
reducer invocation. A failed `next` skips the statements after it unless your code
explicitly handles the exception.

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
forwarding by default; a post exception propagates without rolling back
committed state.
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
subscriber, and nested-dispatch failures. This also applies when calling a retained
`ctx.dispatch` callback or `store.dispatch` directly, including dispatch to another
store. The failure is associated with the handler making the call, regardless of
where the callback was obtained.

Once such a call fails, any later exception in that handler invocation also
propagates, even if the handler catches and translates the original error.
Explicitly catching an error and returning normally remains possible. A failure
handled entirely inside a nested dispatch does not disable the caller's recovery.
Failure tracking ends with the handler invocation; it does not carry over to later
dispatches.

Library definition, dispatch, ambiguity, and return-contract errors are not
recovered. `KeyboardInterrupt`, `SystemExit`, and other `BaseException` subclasses
are not recovered either. Direct method calls (including `super()`) use ordinary
Python exception behavior; recovery and cancellation handling belong to the chain.
Plain middleware factories retain their own exception policy.

## Subscriptions

Use middleware to react to specific actions. `subscribe` is optional and useful
for consumers such as a UI that need notification after reducer dispatch,
regardless of which action caused it.

```python
subscription_store = Store[Count, Add](
    initial_state=Count(), reducer=MachineReducer[Count](Counter()),
)

def on_update() -> None:
    print(subscription_store.get_state())

unsubscribe = subscription_store.subscribe(on_update)
subscription_store.dispatch(Add(1))  # Calls on_update after committing state.
unsubscribe()                       # Safe to call more than once.
```

Listeners receive no arguments and return `None`. Registration does not invoke
them immediately. Every successful reducer dispatch notifies listeners, even if
the state object is unchanged. An action consumed before reaching the reducer
does not notify them on its own.

The store snapshots listeners for each notification pass. Changes to subscriptions
during that pass affect later passes. A listener exception stops the current pass
and propagates without rolling back state.

There is no built-in slice subscription or previous-state argument. A listener can
retain its previously observed value and compare it to `get_state()`. However, an
earlier listener may synchronously dispatch again before it runs, so observed
values are not guaranteed to represent every intermediate state.

## Async work

The store and handlers are synchronous. Start and manage async work in your
middleware using your application's event loop or workers, then dispatch a
completion action. The application owns task tracking, cancellation, error
handling, and stale-result decisions.

You may retain `ctx.dispatch` for later use. You may not retain `ctx.next` for
later forwarding: it expires when the handler returns. All store access must
happen on the thread that created the store. A worker must schedule its completion
dispatch back onto that thread using the application's scheduling mechanism.

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

## API reference

All names below are exported from `typomata_redux`. `S` denotes a state type and
`A` an action type or union.

| API | Purpose |
| --- | --- |
| `Store[S, A](initial_state=..., reducer=..., middleware=())` | Own state and assemble the middleware chain. |
| `store.dispatch(action) -> None` | Dispatch synchronously through the entire chain. |
| `store.get_state() -> S` | Read the current state object. |
| `store.subscribe(listener) -> Callable[[], None]` | Register a no-argument listener and return an unsubscribe function. |
| `MachineReducer[S](machine)` | Adapt Typomata transitions to a reducer accepting `BaseAction`. |
| `combine_reducers(StateClass, field=reducer, ...)` | Infer the root type and compose dataclass slices. |
| `CombinedReducer[S](StateClass, field=reducer, ...)` | Construct composition with an explicit root type. |
| `Middleware[S, A]` | Base class for annotated middleware handlers. |
| `StoreAPI[S, A]` | Context exposing `get_state` and `dispatch`. |
| `MiddlewareContext[S, A]` | Context also exposing one-use `next`. |
| `intercept`, `intercept_pre`, `intercept_post` | Register handlers; each accepts `catch_exceptions=False`. |
| `Dispatch[A]`, `MiddlewareFactory[S, A]` | Callable aliases for custom middleware. |

| Exception | Meaning |
| --- | --- |
| `CancelAction` | Raised by a handler to consume an action and unwind normally. |
| `MiddlewareError` | Explicit failure that bypasses automatic recovery. |
| `DefinitionError` | Invalid middleware or composition definition. |
| `AmbiguousHandlerError` | Multiple handlers match where only one is allowed. |
| `DispatchError` | Invalid store access or forwarding, such as a second `next` call. |

Runtime action and return-value checks can also raise `TypeError`. See
[exception handling](#middleware-exception-handling) for recovery boundaries.

## Static typing boundaries

- Store dispatch, middleware context dispatch, state access, and direct handler
  calls retain precise static types. Run mypy or Pyright on application code.
- `MachineReducer[S]` is a declaration by the caller: Typomata's `BaseStateMachine`
  is not generic, so a checker cannot prove that all registered transitions stay
  inside `S`. Use the union of possible slice states. Typomata checks each actual
  transition result against its return annotation; composition additionally checks
  declarations and results against the dataclass field type.
- Composition field names and heterogeneous reducer wiring remain runtime-checked.
- Middleware handler action annotations determine routing. Their relation to the
  middleware/store's generic action union is not exhaustively checked at registration.
- A handler's context annotation is not statically linked to its owning middleware.
  For example, a handler in `Middleware[Count, Add]` can incorrectly declare
  `StoreAPI[Profile, Add]` and still pass mypy and Pyright. The actual state is
  `Count`; reading a Profile-only attribute will fail at runtime. Keep context
  state/action arguments consistent with the middleware. Application-level context
  aliases can reduce repetition, but do not enforce this relationship.

Types are declared through generics and handler annotations, without separate
`states=` or `actions=` configuration. Slice reducers accept `BaseAction`, so
individual slices do not need to import the application's full action union.
The store's generic parameters express static contracts rather than runtime
validation of the exact union members.

Runtime checks remain where existing information is sufficient: transition
annotations, action/state base classes, dataclass field types, ambiguity, and
middleware forwarding rules. Passing an unrelated `BaseAction` through untyped
code is not rejected merely because it is outside the store's static action union.
A plain reducer's result is checked as `BaseState`, not against the store's generic
state union. Frozen dataclasses and immutable nested values are recommended;
checks do not prove purity or deep immutability.

## Dispatch contract

- `[First(), Second()]` runs First before Second, then reduces, commits state,
  notifies subscribers, and returns through Second and First.
- `ctx.next(action)` continues downstream. `ctx.dispatch(action)` starts a new
  dispatch through the entire chain. Both return `None`.
- If no middleware handler matches, the action is forwarded automatically. A
  matching manual handler may forward a replacement or consume by not forwarding.
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

## Development

From the project checkout after `uv sync`:

```bash
uv run python -m unittest discover -s tests -v
uv run mypy
uv run pyright
uv run python scripts/verify_typing.py
uv run python examples/counter.py
uv build
uv run python scripts/verify_distribution.py
```

`verify_typing.py` checks valid consumers and verifies negative cases independently
with mypy and Pyright. It copies the fixtures to a temporary directory, removes
both checkers' suppression comments, and checks each expected diagnostic by file,
line, and code. Missing or unexpected diagnostics fail the check. Declaration,
context, and store-wiring mistakes are covered alongside incorrect calls.

Known middleware declaration gaps are kept in `tests/typing/known_gaps` and reported
separately. A passing run does not claim those declarations are rejected. If either
checker begins rejecting a known gap, the check fails so the fixture and documented
limitation can be reviewed.

The distribution verification builds wheels from source archives, runs tests and
the example against an installed package, and runs the same positive and negative
typing checks outside the source tree against that installation. CI runs both
source and installed-package checks.

## Migrating the earlier API

- `MachineReducer[S, A](machine, states=S, actions=A)` becomes `MachineReducer[S](machine)`.
- `CombinedReducer[S, A](S, ...)` becomes `CombinedReducer[S](S, ...)`, or use `combine_reducers(S, ...)`.
- `Store[S, A](..., states=S, actions=A)` becomes `Store[S, A](...)`.
- Remove narrow-action routing adapters. Slice dispatch accepts `BaseAction` and
  ignores unmatched actions; handler signatures remain narrow.

The old schema keywords are removed, not silently accepted. Initial state still
comes from `initial_state`. Runtime enforcement of the store's exact generic
state/action vocabulary is no longer provided.

## Scope

This implementation adapts public `transition_map()` snapshots and invokes
Typomata's decorated methods. It does not use private Typomata internals or modify
Typomata. There are no diagrams yet; static action-handler diagrams are the final
planned feature.
