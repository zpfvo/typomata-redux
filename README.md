# Typomata Redux

Synchronous, typed Redux with annotation-based reducers and middleware. Write
ordinary reducer functions, compose them over nested state, and keep side effects
in middleware. State and action types need no marker base classes.

The project name is still provisional.

- [Setup](#setup), [quick start](#quick-start), and [exhaustive action handling](#exhaustive-action-handling)
- [Combining reducers](#combining-reducers) and [calling domain logic](#calling-domain-logic-from-reducers)
- [Choosing middleware](#choosing-a-middleware-decorator) and [passing dependencies](#passing-dependencies-to-middleware)
- [Required middleware coverage](#required-middleware-coverage)
- [Exception handling](#middleware-exception-handling), [subscriptions](#subscriptions), and [async work](#async-work)
- [API reference](#api-reference), [typing limits](#static-typing-boundaries), and [development](#development)

## How the pieces fit

An **action** describes what happened. **State** holds the application's data;
prefer frozen dataclasses and immutable nested values. A **reducer** takes state
and an action and returns the next state without side effects. A **store** owns
the current state and sends dispatched actions through middleware to the reducer.

In composition, each function receives only actions matching its action annotation.
An unrelated action preserves that slice's state object. Multiple slices can handle
the same action. Middleware also selects handlers by action annotation and reads
state through its context. An unmatched middleware action continues downstream.

With `middleware=[First(), Second()]`, normal dispatch proceeds in this order:

```text
First pre → Second pre → reducer → commit state → subscribers
                                                ↓
First post ← Second post ←───────────────────────┘
```

Manual middleware controls forwarding explicitly. Consumption and exceptions can
shorten this sequence; the sections below explain how post-handlers unwind.

## Setup

Requires Python 3.10+. Install from this checkout:

```bash
python -m pip install .
```

Or use uv without the development dependencies:

```bash
uv sync --no-dev
uv run --no-dev python examples/counter.py
```

## Quick start

This complete example defines plain state/action dataclasses, a reducer, and manual
middleware. The reducer's action union stays narrow and supports exhaustive checking:

```python
from dataclasses import dataclass
from typing_extensions import assert_never
from typomata_redux import FunctionReducer, Middleware, MiddlewareContext, Store, intercept

@dataclass(frozen=True)
class Count:
    value: int = 0

@dataclass(frozen=True)
class Add:
    amount: int

@dataclass(frozen=True)
class Reset:
    pass

CounterAction = Add | Reset

def counter(state: Count, action: CounterAction) -> Count:
    if isinstance(action, Add):
        return Count(state.value + action.amount)
    if isinstance(action, Reset):
        return Count()
    assert_never(action)

class Log(Middleware[Count, CounterAction], manual_actions=CounterAction):
    @intercept
    def log(self, action: CounterAction, ctx: MiddlewareContext[Count, CounterAction]) -> None:
        print("before", ctx.get_state())
        ctx.next(action)
        print("after", ctx.get_state())

store = Store[Count, CounterAction](
    initial_state=Count(), reducer=counter, middleware=[Log()],
)
store.dispatch(Add(2))  # Returns None.
assert store.get_state() == Count(2)
```

`Store[S, A]` exposes `get_state() -> S` and `dispatch(action: A) -> None`.
`ctx.next(action)` forwards downstream. Returning without calling it consumes the
action, then earlier middleware resumes normally.
`manual_actions=CounterAction` declares the actions this middleware must handle.
Every used handler phase requires a [coverage declaration](#required-middleware-coverage).

Pass annotated reducer functions directly to both `Store` and `combine_reducers`.
Both adapt them automatically: unrelated actions preserve state identity, and
state inputs and returned values are validated. A root reducer may accept a
narrower action union than the store; dispatch retains the store's precise union.
The [runnable example](examples/counter.py) composes two slices and dispatches a
follow-up action from middleware.

### Exhaustive action handling

A narrow action union makes it possible to catch forgotten cases when the code
changes. In the quick start, each `isinstance` branch handles one member of
`CounterAction` and returns. At the final `assert_never(action)`, mypy and Pyright
can therefore prove that no possible action remains. `assert_never` accepts only
the `Never` type, which has no possible values.

Suppose you add an action and extend the quick start's union:

```python
@dataclass(frozen=True)
class Decrement:
    amount: int

CounterAction = Add | Reset | Decrement
```

With `counter` otherwise unchanged, `action` can still be a `Decrement` at
`assert_never(action)`. Running either type checker now reports an error there:
`Decrement` cannot be passed where `Never` is expected. Add its branch before
`assert_never` to make the reducer exhaustive again:

```python
if isinstance(action, Decrement):
    return Count(state.value - action.amount)
```

Keep the final `assert_never` so future additions receive the same check. A branch
may explicitly return `state` when an action should do nothing. A catch-all
`return state` would silently accept forgotten cases and lose this check.

This works with independent slices: `combine_reducers` and `FunctionReducer`
filter out unrelated actions before calling the function, preserving state
identity. Each reducer can exhaustively handle its own narrow union without
listing the entire application's actions. Adding an action to the application
union alone does not require every slice to handle it; extending a slice's union
does.

Exhaustiveness is checked when you run a type checker; a union annotation alone
does not require exhaustive branches. If execution reaches `assert_never`, it
raises an `AssertionError`. The check covers the members of the declared union;
it does not prove that every application action has a registered reducer.

## Combining reducers

Pass annotated functions directly, using the root dataclass's field names:

```python
from typomata_redux import combine_reducers

@dataclass(frozen=True)
class AppState:
    count: Count = Count()
    title: str = "Counter"

reducer = combine_reducers(AppState, count=counter)
app = Store[AppState, CounterAction](initial_state=AppState(), reducer=reducer)
app.dispatch(Add(3))
assert app.get_state().count == Count(3)
assert app.get_state().title == "Counter"
```

Composition wraps each plain function in `FunctionReducer` once, reading its
state, action, and result annotations. The function's own signature stays intact:
there is no separate action list or broad input annotation to maintain. Each action
is considered for every slice, and functions run only when their action annotation
matches, including subclasses. Shared actions such as `Reset` can reach several
slices. A function accepting `object` as its action type handles every action.

Matching functions receive their own original slice state, in keyword order.
The combined reducer returns the identical root object when all slices retain
identity; otherwise it rebuilds the dataclass once, sharing unchanged branches.
Unconfigured fields keep their values. Comparison uses identity, not equality.

Composition nests to match your application's state shape:

```python
@dataclass(frozen=True)
class NestedState:
    feature: AppState = AppState()

nested = combine_reducers(NestedState, feature=reducer)
assert nested(NestedState(), Add(2)).feature.count == Count(2)
```

Plain functions and bound methods must declare exactly two required positional
parameters (state, action) and a result annotation. Names may differ. Supported
annotations are concrete classes, unions, and `Annotated` wrappers. State can be a
scalar such as `int`, a dataclass, or a union of state classes. `Any`, unresolved
names, generic TypeVars, parameterized types such as `list[Item]`, and protocols
are rejected. Wrap collection state in a dataclass when needed. Unannotated
lambdas, callable instances, partials, and async/generator reducers are not supported
at either the root or a slice. Stores validate initial state before constructing
middleware, without calling reducers. A function's result annotation must fit its
accepted state types so the result can be passed back on the next dispatch.
Reducers must return state immediately; returned
awaitables and generators are rejected.

Annotations resolve in module scope and, for bound methods, the declaring context
available on the bound owner. Function-local forward references are not searched
for automatically. Definitions are snapshotted at adapter construction.

Each function must accept every state variant declared for its field; filtering
is by action type. Its declared return types must fit that field. Construction
rejects incompatible annotations and unknown field names; dispatch checks field
inputs and function results. These checks are shallow and do not prove purity or
deep immutability. Exceptions from matching functions propagate, never become
no-ops, and prevent the root state from being committed.

Existing `FunctionReducer` and `CombinedReducer` instances can also be passed as
children. For explicit root typing use
`CombinedReducer[AppState](AppState, count=counter)`. The helper infers that type
from `AppState`. Adapted and combined reducers accept `object`; the store retains
its own precise action union.

Rebuilding uses `dataclasses.replace`, including constructor/`__post_init__` behavior.
Fields with `init=False` cannot be targeted and may be recomputed. Required `InitVar`
arguments need a custom root reducer. In-place mutation and side effects cannot
be rolled back; keep reducers pure.

## Calling domain logic from reducers

Reducers can call pure helper functions, bound methods, or an application-owned
state machine. Their ordinary state/action/result annotations remain the library's
interface. For example, building on the quick start and composition examples:

```python
from typomata_redux import combine_reducers

class CounterLogic:
    def add(self, state: Count, action: Add) -> Count:
        return Count(state.value + action.amount)

logic = CounterLogic()

def counter_with_logic(state: Count, action: CounterAction) -> Count:
    if isinstance(action, Add):
        return logic.add(state, action)
    if isinstance(action, Reset):
        return Count()
    assert_never(action)

reducer = combine_reducers(AppState, count=counter_with_logic)
```

The wrapper preserves exhaustive action handling and lets composition filter
unrelated actions. Pure bound methods can also be passed directly as slice reducers
when their annotated signatures fit the field.

If using a state-machine library, the application installs and configures it, then
calls it from a reducer using the same pattern. Store the machine's current state
value in Redux state; keep its behavior object outside that state. Transitions must
be pure and return state values without mutating existing snapshots. The reducer
must accept every state variant declared for its slice and decide when a state/action
pair should be a no-op. Engine errors propagate unless the application handles them
explicitly. If an engine returns a broad state type, validate and narrow its result
before returning it from a reducer with a more specific annotation.

Inspection describes the reducer's own declarations. It does not inspect the
internal transitions of helpers or external state machines.

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
class ObserveCount(Middleware[Count, Add], manual_actions=Add):
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

## Required middleware coverage

Every annotated middleware class must declare the actions each used phase handles:

| Handler decorator | Required class keyword |
| --- | --- |
| `@intercept_pre` | `pre_actions=...` |
| `@intercept_post` | `post_actions=...` |
| `@intercept` | `manual_actions=...` |

Use an action class or a union; `Annotated` wrappers are also supported. Omit unused
phases. Action classes can live in `counter/actions.py`, while the phase unions live
alongside the middleware in `counter/middleware.py`. Building on the quick start:

```python
from typomata_redux import StoreAPI, intercept_pre, intercept_post

CounterPreAction = Add | Reset
CounterPostAction = Add
CounterAPI = StoreAPI[Count, CounterAction]

class CounterEffects(
    Middleware[Count, CounterAction],
    pre_actions=CounterPreAction,
    post_actions=CounterPostAction,
):
    @intercept_pre
    def before_add(self, action: Add, ctx: CounterAPI) -> None:
        print("adding", action.amount)

    @intercept_pre
    def before_reset(self, action: Reset, ctx: CounterAPI) -> None:
        print("resetting")

    @intercept_post
    def after_add(self, action: Add, ctx: CounterAPI) -> None:
        print("count", ctx.get_state().value)

effects = CounterEffects()  # No action declarations in the constructor.
```

When Python defines the class, usually during import, the library checks that
every declared action has a matching handler in that phase and every handler's
action annotation fits the phase's declaration. For example, adding `Decrement`
to `CounterPreAction` without adding a pre-handler raises:

```text
DefinitionError: CounterEffects: missing pre handler for Decrement
```

Adding a handler for an undeclared action also fails. A declaration with no handlers
fails even if that entire phase was forgotten. The unions must be maintained
independently of the handlers so the check can detect omissions. Adding an action
only to the application's overall union does not require every middleware to handle
it. Unrelated actions continue downstream. Constructor dependencies are unchanged.

One handler may cover several union members, and subclasses match superclass
annotations. A handler accepting `object` needs `object` in the phase declaration;
it covers all actions. A broad declaration cannot be covered solely by handlers
for some of its subclasses. Known overlaps within a phase, and between manual and
automatic handlers, fail at class definition. Runtime ambiguity checks remain for
other overlaps, such as a later class inheriting two separately handled action types.

**Coverage is checked by the library at class definition.** Mypy and Pyright do not
prove coverage across separate decorated methods. For a handler that accepts a
union, you can additionally use [the `assert_never` pattern](#exhaustive-action-handling)
to statically check its internal branches. For example, a pre-handler can return
after each `isinstance` branch and end with `assert_never(action)`. Declaring coverage
does not inspect method bodies or guarantee that a handler will execute: earlier
middleware can consume an action, and errors can prevent later phases.

Subclasses inherit the phase declarations and handlers from their direct bases.
Multiple bases contribute the union of their requirements. Each subclass is checked
again after method overrides are resolved. To add or change handled actions, provide
a replacement declaration for that phase. To remove a phase, use e.g.
`pre_actions=None` and remove its registered handlers with undecorated overrides.
Clearing the declaration alone leaves those handlers invalid. Empty middleware
classes need no declarations. Plain middleware factories use their own routing and
are outside this coverage contract.

### Middleware declaration consistency

The `S` and `A` arguments of every handler context must match those of its owning
`Middleware[S, A]`. Each phase declaration must be contained in `A`. Contradictions
raise `DefinitionError` during class creation, including overrides and conflicting
multiple-inheritance contracts. Union member order and `Annotated` metadata do not
change this comparison. Concrete state and action arguments use the same supported
class/union vocabulary as reducers; `Any` is rejected.

Generic bases may use TypeVars for their state and context arguments. Subclasses
substitute these through every inheritance level and are checked again. A directly
specialized generic instance, such as `Effects[Count]()`, is checked when bound to
a store, after Python has supplied its generic arguments. Unresolved generic
handlers cannot be bound. Routed handler actions and phase coverage declarations
still require concrete classes or unions. Use a concrete subclass when you want
all checks to run at class definition.

## Automatic pre/post handlers

Use `@intercept_pre` and `@intercept_post` when forwarding should be automatic:

```python
from typomata_redux import StoreAPI, intercept_pre, intercept_post

class Log(Middleware[Count, Add], pre_actions=Add, post_actions=Add):
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
conflict. Known action conflicts fail during class creation; other overlapping
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

class SaveCount(Middleware[Count, Add], post_actions=Add):
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
        reducer=counter,
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

class Audit(Middleware[Count, Add], pre_actions=Add):
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
    initial_state=Count(), reducer=counter,
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
order. Store boundaries check that handlers return `None`; the store's generic
state and action contracts are enforced statically, not by runtime union checks.
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
| `FunctionReducer(function)` | Explicit adapter for standalone invocation; stores and composition adapt functions automatically. |
| `combine_reducers(StateClass, field=reducer, ...)` | Infer the root type and compose dataclass slices. |
| `CombinedReducer[S](StateClass, field=reducer, ...)` | Construct composition with an explicit root type. |
| `Middleware[S, A]` | Annotated middleware base; declare `pre_actions`, `post_actions`, and/or `manual_actions` as class keywords for used phases. |
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

- Store dispatch, context dispatch, state access, and direct function/handler calls
  retain precise static types. A narrow function body can use `assert_never` to
  [check exhaustive handling](#exhaustive-action-handling) as its action union grows.
- `FunctionReducer` infers the state type from the function signature; composition
  infers the root type from its dataclass. Heterogeneous field/reducer wiring is
  not statically linked to field names. Runtime checks use the field and function
  annotations to reject incompatible wiring.
- Handler action/context annotations are not statically linked to their owning
  `Middleware[S, A]`. A handler in `Middleware[Count, Add]` can incorrectly declare
  `StoreAPI[AppState, Add]` and pass mypy/Pyright. Class creation now rejects that
  mismatch with `DefinitionError`, before any handler executes. Context state and
  action arguments must match the owner after resolving aliases, union order,
  `Annotated`, and inherited generic substitutions. The known-gap fixtures track
  the remaining **static** limitation; runtime tests separately verify rejection.
- Phase coverage declarations validate registrations at class definition. They
  must fit the owning middleware's action vocabulary. This is a runtime guarantee;
  type checkers still do not link the declarations. Declarations are snapshotted; rebinding a union
  alias later does not change an existing class's contract.
- `Store[S, A]` does not inspect its generic arguments or require base-class markers.
  Untyped callers can dispatch objects outside `A`. Adapted root and slice reducers
  treat unmatched actions as no-ops and validate declared state/result types.
  Validate external data before dispatch.

### Sharing middleware type aliases

Define the middleware base and context aliases once alongside your application's
state and action types, then import them in middleware modules. This keeps handlers
from repeatedly spelling out the state and action union. Building on the quick start:

```python
from typing import TypeAlias
from typomata_redux import StoreAPI, intercept_post

class NoOp:
    pass

CounterActions: TypeAlias = Add | NoOp
CounterMiddleware: TypeAlias = Middleware[Count, CounterActions]
CounterAPI: TypeAlias = StoreAPI[Count, CounterActions]
CounterContext: TypeAlias = MiddlewareContext[Count, CounterActions]

class ValidateAmount(CounterMiddleware, manual_actions=Add):
    @intercept
    def validate(self, action: Add, ctx: CounterContext) -> None:
        if action.amount <= 0:
            return  # Consume this action.
        ctx.next(action)

class ReportCount(CounterMiddleware, post_actions=Add):
    @intercept_post
    def report(self, action: Add, ctx: CounterAPI) -> None:
        print(ctx.get_state().value)  # Statically typed as int.

counter_store = Store[Count, CounterActions](
    initial_state=Count(),
    reducer=counter,
    middleware=[ValidateAmount(), ReportCount()],
)
counter_store.dispatch(Add(2))  # Prints 2.
counter_store.dispatch(NoOp())  # No matching handlers; state stays unchanged.
assert counter_store.get_state() == Count(2)
```

Use `CounterAPI` for pre/post handlers and `CounterContext` for manual handlers.
Each handler can still select a narrow action such as `Add`, while context dispatch
accepts the full `CounterActions` union. Subclassing the alias also leaves normal
constructors and injected instance attributes available.

These aliases reduce repetition. Both context aliases must agree with
`CounterMiddleware`; a mismatched context from another application now fails at
class definition. This adds a runtime guarantee, not a new static guarantee.

## Dispatch contract

- `[First(), Second()]` runs First before Second, then reduces, commits state,
  notifies subscribers, and returns through Second and First.
- `ctx.next(action)` continues downstream. `ctx.dispatch(action)` starts a new
  dispatch through the entire chain. Both return `None`.
- If no middleware handler matches, the action is forwarded automatically. A
  matching manual handler may forward a replacement or consume by not forwarding.
- An annotated handler's `next` is valid once, during that invocation. Additional
  or delayed actions use `dispatch`. No batching or async dispatch API is provided.
- Missing coverage, handlers outside their phase vocabulary, and known same-phase
  or manual/automatic conflicts fail at class creation. Other overlaps, including
  later multiple-inheritance action types, raise `AmbiguousHandlerError` at dispatch. No name-order or
  most-specific-match rule is applied. A broad logger belongs in its own middleware.
- Handlers are synchronous instance methods with three required positional
  parameters: receiver, action, and context (`MiddlewareContext[S, A]` for manual
  handlers, `StoreAPI[S, A]` for automatic pre/post handlers); return annotation
  is `None`. Parameter names may differ; positional-only parameters are supported.
  Static/class methods, generators, async handlers, and unsupported annotations
  are rejected. Postponed annotations resolve in module/declaring-class scope;
  function-local forward references are not searched for automatically.
- Inherited handlers and decorated overrides work. An undecorated override removes
  the inherited registration; update its phase declaration if coverage changes.
  Direct calls preserve their static signatures and
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

The complete development suite runs from this checkout:

```bash
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked mypy
uv run --locked pyright
uv run --locked python scripts/verify_typing.py
uv run --locked python examples/counter.py
uv build
uv run --locked python scripts/verify_distribution.py
```

The development group contains only the type checkers. No sibling repository or
state-machine package is required.

`verify_typing.py` verifies valid consumers and explicit negative cases independently
with mypy and Pyright. It copies fixtures outside the source tree, removes both
checkers' suppression comments, and compares diagnostics by file, line, and code.
Missing or unexpected diagnostics fail. Known middleware declaration gaps are
reported separately; if a checker starts rejecting one, verification fails so the
fixture and documented limitation can be reviewed.

Distribution verification builds a wheel from the source archive and installs it
in a clean environment. It runs the entire runtime suite, example, and consumer
typing checks against the installed package with Typomata absent. CI runs these
checks on Python 3.10–3.14.

## Migrating the earlier API

Root reducers now use the same annotation and validation contract as composed
slices. Replace unannotated lambdas with annotated functions. Unsupported callable
instances, partials, parameterized annotations, and `Any` now fail at root store
construction too. An unrelated action no longer invokes a narrow root function.
Invalid initial state fails before middleware factories run, and invalid results
fail before committing state. Existing `FunctionReducer` wrappers still work but
are unnecessary when passing functions to `Store` or `combine_reducers`.


From the previous version of this project:

- Add class keywords for each used middleware phase: `pre_actions=...`,
  `post_actions=...`, and/or `manual_actions=...`. Use the union of actions that
  phase is intended to handle; constructors still receive only your dependencies.
  Missing declarations or handlers now raise `DefinitionError` at class definition.
  Existing inherited declarations are checked after overrides, and known subclass
  overlaps now fail during class definition instead of waiting for dispatch.
- Plain slice functions can use their own narrow action union. Pass them directly
  to `combine_reducers`; it derives the runtime filter from the annotation.
- Add state/action/result annotations to handwritten slices. Unannotated lambdas
  previously accepted by composition now fail at construction.
- State and action types need no marker base classes. Existing subclasses remain
  ordinary Python types. Use `object` for a handler accepting all actions.
- `MachineReducer` and the built-in Typomata integration have been removed. Replace
  adapter instances with annotated reducer functions that call your domain logic.
  Define any state-dependent no-op behavior explicitly; direct engine calls may
  raise for unmatched transitions. See [calling domain logic](#calling-domain-logic-from-reducers).
- Generic state/action contracts are static. The former runtime base-class checks
  are removed; validate untyped external data explicitly.

The older `states=` and `actions=` schema keywords remain removed. Initial state
still comes from `Store(initial_state=...)`. There is no automatic initial-state
factory or action-union inference from the application's slices.

## Scope

The working name remains `typomata-redux`. State-machine use belongs to application
reducers. Internal inspection describes function reducers, middleware handlers,
and nested composition using dispatch's own declarations.
Plain root callables and custom dispatch overrides remain opaque. Inspection does
not execute handlers or predict side effects, and its metadata has no public
stability guarantee. Static action-handler diagrams remain the final planned feature.
