from __future__ import annotations

import threading
import unittest
from dataclasses import dataclass
from typing import Annotated

from typomata import BaseAction, BaseState, BaseStateMachine, transition
from typomata_redux import (
    AmbiguousHandlerError, DefinitionError, DispatchError, FunctionReducer, MachineReducer,
    Middleware, MiddlewareContext, Store, intercept,
)


@dataclass(frozen=True)
class State(BaseState):
    value: int = 0


@dataclass(frozen=True)
class Add(BaseAction):
    amount: int = 1


@dataclass(frozen=True)
class Ignore(BaseAction):
    pass


class Foreign(BaseAction):
    pass


@dataclass(frozen=True)
class Finished(BaseState):
    value: int


class Finisher(BaseStateMachine):
    @transition
    def finish(self, state: State, action: Add) -> Finished:
        return Finished(state.value + action.amount)


Actions = Add | Ignore
Context = MiddlewareContext[State, Actions]


class Counter(BaseStateMachine):
    @transition
    def add(self, state: State, action: Add) -> State:
        return State(state.value + action.amount)


def adapter(machine=None):
    return MachineReducer[State](machine or Counter())


def store(*middleware, reducer=None):
    return Store[State, Actions](
        initial_state=State(), reducer=reducer or adapter(),
        middleware=middleware,
    )


class Recording(Middleware[State, Actions]):
    def __init__(self, name, events):
        self.name, self.events = name, events

    @intercept
    def handle(self, action: Actions, ctx: Context) -> None:
        self.events.append((self.name, "before", ctx.get_state().value))
        ctx.next(action)
        self.events.append((self.name, "after", ctx.get_state().value))


class ReduxTests(unittest.TestCase):
    def test_reduce_and_identity_noop(self):
        subject = store()
        old = subject.get_state()
        self.assertIsNone(subject.dispatch(Ignore()))
        self.assertIs(subject.get_state(), old)
        self.assertIsNone(subject.dispatch(Add(4)))
        self.assertEqual(subject.get_state(), State(4))
        self.assertEqual(old, State())

    def test_order_and_commit_before_notification_and_unwind(self):
        events = []
        subject = store(Recording("first", events), Recording("second", events))
        subject.subscribe(lambda: events.append(("listener", subject.get_state().value)))
        subject.dispatch(Add())
        self.assertEqual(events, [
            ("first", "before", 0), ("second", "before", 0), ("listener", 1),
            ("second", "after", 1), ("first", "after", 1),
        ])

    def test_consumption_and_unmatched_forwarding(self):
        events = []

        class Consume(Middleware[State, Actions]):
            @intercept
            def consume(self, action: Add, ctx: Context) -> None:
                events.append("consumed")

        subject = store(Recording("outer", events), Consume())
        subject.subscribe(lambda: events.append("notified"))
        subject.dispatch(Add())
        self.assertEqual(events, [("outer", "before", 0), "consumed", ("outer", "after", 0)])
        events.clear()
        subject.dispatch(Ignore())
        self.assertEqual(events, [("outer", "before", 0), "notified", ("outer", "after", 0)])

    def test_replacement_only_goes_downstream(self):
        seen = []

        class Replace(Middleware[State, Actions]):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                seen.append(action.amount)
                ctx.next(Add(10))

        subject = store(Replace())
        subject.dispatch(Add())
        self.assertEqual(seen, [1])
        self.assertEqual(subject.get_state(), State(10))

    def test_nested_dispatch_restarts_entire_chain(self):
        events = []

        class FollowUp(Middleware[State, Actions]):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                ctx.next(action)
                if action.amount == 1:
                    ctx.dispatch(Add(2))

        subject = store(Recording("log", events), FollowUp())
        subject.dispatch(Add())
        self.assertEqual(events, [
            ("log", "before", 0), ("log", "before", 1),
            ("log", "after", 3), ("log", "after", 3),
        ])

    def test_untyped_unrelated_objects_follow_unmatched_noop_path(self):
        events = []
        subject = store(Recording("log", events))
        for action in ({}, None, 42):
            with self.subTest(action=action):
                old = subject.get_state()
                subject.dispatch(action)
                self.assertIs(subject.get_state(), old)
        self.assertEqual(events, [])

    def test_untyped_unrelated_replacement_is_an_unmatched_noop(self):
        class Bad(Middleware[State, Actions]):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                ctx.next(object())

        events = []
        subject = store(Bad(), Recording("later", events))
        subject.dispatch(Add())
        self.assertEqual(events, [])
        self.assertEqual(subject.get_state(), State())

    def test_next_once_and_no_rollback_after_commit(self):
        class Twice(Middleware[State, Actions]):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                ctx.next(action)
                ctx.next(action)

        subject = store(Twice())
        with self.assertRaises(DispatchError):
            subject.dispatch(Add())
        self.assertEqual(subject.get_state(), State(1))

    def test_next_expires_but_dispatch_remains_available(self):
        saved = []

        class Save(Middleware[State, Actions]):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                saved.append(ctx)

        subject = store(Save())
        subject.dispatch(Add())
        with self.assertRaises(DispatchError):
            saved[0].next(Add())
        saved[0].dispatch(Ignore())
        self.assertEqual(subject.get_state(), State())

    def test_plain_middleware(self):
        events = []

        def factory(api, next_dispatch):
            def handle(action):
                events.append(api.get_state().value)
                next_dispatch(action)
            return handle

        subject = store(factory)
        subject.dispatch(Add())
        self.assertEqual(events, [0])
        self.assertEqual(subject.get_state(), State(1))

    def test_bindings_are_per_store(self):
        contexts = []

        class Save(Middleware[State, Actions]):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                contexts.append(ctx)
                ctx.next(action)

        middleware = Save()
        first, second = store(middleware), store(middleware)
        first.dispatch(Add(1))
        second.dispatch(Add(7))
        self.assertEqual([ctx.get_state().value for ctx in contexts], [1, 7])
        contexts[0].dispatch(Add(2))
        self.assertEqual(first.get_state(), State(3))
        self.assertEqual(second.get_state(), State(7))

    def test_construction_dispatch_guard(self):
        def factory(api, next_dispatch):
            api.dispatch(Add())
            return next_dispatch

        with self.assertRaisesRegex(DispatchError, "constructing"):
            store(factory)

    def test_reducer_reentrancy_blocked_before_middleware(self):
        events = []

        def reducer(state, action):
            subject.dispatch(Add())
            return state

        subject = store(Recording("log", events), reducer=reducer)
        with self.assertRaises(DispatchError):
            subject.dispatch(Add())
        self.assertEqual(events, [("log", "before", 0)])
        self.assertEqual(subject.get_state(), State())

    def test_reducer_error_and_bad_result_preserve_state(self):
        failure = ValueError("domain error")

        def fail(state, action):
            raise failure

        subject = store(reducer=fail)
        old = subject.get_state()
        with self.assertRaises(ValueError) as caught:
            subject.dispatch(Add())
        self.assertIs(caught.exception, failure)
        self.assertIs(subject.get_state(), old)
        def invalid(state: State, action: Actions) -> State:
            return 4
        subject = store(reducer=FunctionReducer(invalid))
        old = subject.get_state()
        with self.assertRaises(TypeError):
            subject.dispatch(Add())
        self.assertIs(subject.get_state(), old)

    def test_subscriber_snapshot_and_duplicate_registrations(self):
        events = []
        subject = store()

        def first():
            events.append("first")
            unsubscribe()
            subject.subscribe(lambda: events.append("new"))

        subject.subscribe(first)
        listener = lambda: events.append("second")
        unsubscribe = subject.subscribe(listener)
        subject.subscribe(listener)
        old = subject.get_state()
        subject.dispatch(Ignore())
        self.assertEqual(events, ["first", "second", "second"])
        self.assertIs(subject.get_state(), old)
        unsubscribe()
        events.clear()
        subject.dispatch(Ignore())
        self.assertEqual(events, ["first", "second", "new"])

    def test_listener_error_commits_and_stops_notification(self):
        subject = store()
        failure = ValueError("listener")
        seen = []

        def fail():
            raise failure

        subject.subscribe(fail)
        subject.subscribe(lambda: seen.append(True))
        with self.assertRaises(ValueError) as caught:
            subject.dispatch(Add())
        self.assertIs(caught.exception, failure)
        self.assertEqual(subject.get_state(), State(1))
        self.assertEqual(seen, [])

    def test_nested_subscriber_dispatch_order(self):
        subject = store()
        seen = []

        def first():
            if subject.get_state().value == 1:
                subject.dispatch(Add())

        subject.subscribe(first)
        subject.subscribe(lambda: seen.append(subject.get_state().value))
        subject.dispatch(Add())
        self.assertEqual(seen, [2, 2])

    def test_wrong_thread(self):
        subject = store()
        errors = []

        def run():
            try:
                subject.dispatch(Add())
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], DispatchError)
        self.assertEqual(subject.get_state(), State())

    def test_ambiguous_middleware_invokes_neither(self):
        events = []

        class Overlap(Middleware[State, Actions]):
            @intercept
            def all(self, action: BaseAction, ctx: Context) -> None:
                events.append("all")

            @intercept
            def add(self, action: Add, ctx: Context) -> None:
                events.append("add")

        subject = store(Overlap())
        with self.assertRaises(AmbiguousHandlerError):
            subject.dispatch(Add())
        self.assertEqual(events, [])

    def test_duplicate_union_handlers_fail_at_definition(self):
        with self.assertRaises(DefinitionError):
            class Duplicate(Middleware[State, Actions]):
                @intercept
                def all(self, action: Actions, ctx: Context) -> None:
                    pass

                @intercept
                def add(self, action: Add, ctx: Context) -> None:
                    pass

    def test_action_union_is_static_not_runtime_enforced(self):
        # This call is rejected in the typing fixture, but generic arguments are
        # not inspected at runtime. An unmatched BaseAction is an ordinary no-op.
        subject = store()
        old = subject.get_state()
        subject.dispatch(Foreign())
        self.assertIs(subject.get_state(), old)

    def test_union_annotated_subclass_and_renamed_keyword_parameters(self):
        seen = []

        class AnnotatedHandler(Middleware[State, Actions]):
            @intercept
            def handle(self, event: Annotated[Actions, "label"], context: Context) -> None:
                seen.append(event)
                context.next(event)

        class Child(Add):
            pass

        subject = store(AnnotatedHandler())
        subject.dispatch(Child())
        subject.dispatch(Ignore())
        self.assertEqual(len(seen), 2)
        ctx = Context(lambda: State(), lambda action: None, lambda action: None)
        AnnotatedHandler().handle(event=Add(), context=ctx)
        with self.assertRaises(TypeError):
            AnnotatedHandler().handle(event=Foreign(), context=ctx)

    def test_override_super_and_undecorated_removal(self):
        events = []

        class Parent(Middleware[State, Actions]):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                events.append("parent")
                ctx.next(action)

        class Child(Parent):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                events.append("child")
                super().handle(action, ctx)

        subject = store(Child())
        subject.dispatch(Add())
        self.assertEqual(events, ["child", "parent"])

        class Removed(Parent):
            def handle(self, action, ctx):
                raise AssertionError("not registered")

        subject = store(Removed())
        subject.dispatch(Add())
        self.assertEqual(subject.get_state(), State(1))

    def test_handler_bad_result(self):
        class Bad(Middleware[State, Actions]):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                return 42

        subject = store(Bad())
        with self.assertRaisesRegex(TypeError, "return None"):
            subject.dispatch(Add())
        self.assertEqual(subject.get_state(), State())

    def test_malformed_interceptors(self):
        declarations = [
            'def handle(self, action: Add, ctx: Context): pass',
            'def handle(self, action: Add, ctx: Context) -> int: return 1',
            'def handle(self, action: list[Add], ctx: Context) -> None: pass',
            'def handle(self, action: Add, ctx: object) -> None: pass',
            'def handle(self, action: Add, ctx: Context, extra) -> None: pass',
            'def handle(self, *, action: Add, ctx: Context) -> None: pass',
            'def handle(self, action: Add, ctx: Context = None) -> None: pass',
            'async def handle(self, action: Add, ctx: Context) -> None: pass',
            'def handle(self, action: Add, ctx: Context) -> None: yield 1',
            'def handle(self, action: Missing, ctx: Context) -> None: pass',
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration), self.assertRaises(DefinitionError):
                exec('class Bad(Middleware[State, Actions]):\n    @intercept\n    ' + declaration, globals())

    def test_descriptors_rejected_in_both_decorator_orders(self):
        for descriptor in ('staticmethod', 'classmethod'):
            for decorators in (f'@{descriptor}\n    @intercept', f'@intercept\n    @{descriptor}'):
                with self.subTest(decorators=decorators), self.assertRaises(DefinitionError):
                    exec(f'class Bad(Middleware[State, Actions]):\n    {decorators}\n'
                         '    def handle(self, action: Add, ctx: Context) -> None: pass', globals())

    def test_async_plain_functions_rejected(self):
        async def reducer(state, action):
            return state

        async def listener():
            pass

        async def handler(action):
            pass

        with self.assertRaises(DefinitionError):
            store(reducer=reducer)
        with self.assertRaises(DefinitionError):
            store().subscribe(listener)
        with self.assertRaises(DefinitionError):
            store(lambda api, next_dispatch: handler)

    def test_typomata_errors_not_converted_to_noop(self):
        failure = ValueError("domain")

        class Fails(BaseStateMachine):
            @transition
            def handle(self, state: State, action: Add) -> State:
                raise failure

        subject = store(reducer=adapter(Fails()))
        with self.assertRaises(ValueError) as caught:
            subject.dispatch(Add())
        self.assertIs(caught.exception, failure)

        class BadResult(BaseStateMachine):
            @transition
            def handle(self, state: State, action: Add) -> State:
                return 123

        subject = store(reducer=adapter(BadResult()))
        with self.assertRaises(ValueError):
            subject.dispatch(Add())
        self.assertEqual(subject.get_state(), State())

    def test_ambiguous_reducer_no_invocation(self):
        events = []

        class Overlap(BaseStateMachine):
            @transition
            def broad(self, state: BaseState, action: BaseAction) -> State:
                events.append("broad")
                return State()

            @transition
            def narrow(self, state: State, action: Add) -> State:
                events.append("narrow")
                return State()

        subject = store(reducer=adapter(Overlap()))
        with self.assertRaises(AmbiguousHandlerError):
            subject.dispatch(Add())
        self.assertEqual(events, [])

    def test_removed_schema_arguments(self):
        with self.assertRaises(TypeError):
            MachineReducer[State](Counter(), states=State)
        with self.assertRaises(TypeError):
            MachineReducer[State](Counter(), actions=Actions)
        with self.assertRaises(TypeError):
            Store(initial_state=State(), reducer=adapter(), states=State)
        with self.assertRaises(TypeError):
            Store(initial_state=State(), reducer=adapter(), actions=Actions)

    def test_state_category_transition_and_unhandled_pair(self):
        reducer = MachineReducer[State | Finished](
            Finisher(),
        )
        subject = Store[State | Finished, Actions](
            initial_state=State(), reducer=reducer,
        )
        subject.dispatch(Add(3))
        old = subject.get_state()
        self.assertEqual(old, Finished(3))
        subject.dispatch(Add(8))
        self.assertIs(subject.get_state(), old)

    def test_state_variant_without_any_transition_is_a_noop(self):
        reducer = MachineReducer[State | Finished](Counter())
        old = Finished(4)
        self.assertIs(reducer(old, Add()), old)
        self.assertIs(reducer(old, Foreign()), old)

    def test_context_expires_when_middleware_raises(self):
        saved = []
        failure = ValueError("effect failed")

        class Fails(Middleware[State, Actions]):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                saved.append(ctx)
                ctx.next(action)
                raise failure

        subject = store(Fails())
        with self.assertRaises(ValueError) as caught:
            subject.dispatch(Add())
        self.assertIs(caught.exception, failure)
        self.assertEqual(subject.get_state(), State(1))
        with self.assertRaises(DispatchError):
            saved[0].next(Add())


if __name__ == "__main__":
    unittest.main()
