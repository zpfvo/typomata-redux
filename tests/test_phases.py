from __future__ import annotations

import unittest

from typomata import BaseAction
from typomata_redux import (
    AmbiguousHandlerError, DefinitionError, Middleware, StoreAPI,
    intercept, intercept_pre, intercept_post,
)
from test_redux import Actions, Add, Context, Ignore, State, store

API = StoreAPI[State, Actions]


class PhaseTests(unittest.TestCase):
    def test_order_state_and_context_capabilities(self):
        events = []

        class Phases(Middleware[State, Actions]):
            def __init__(self, name):
                self.name = name

            @intercept_pre
            def before(self, action: Actions, ctx: API) -> None:
                events.append((self.name, 'pre', ctx.get_state().value))
                self.assert_context(ctx)

            @intercept_post
            def after(self, action: Actions, ctx: API) -> None:
                events.append((self.name, 'post', ctx.get_state().value))
                self.assert_context(ctx)

            def assert_context(self, ctx):
                if hasattr(ctx, 'next'):
                    raise AssertionError('automatic context exposes next')

        subject = store(Phases('a'), Phases('b'))
        subject.subscribe(lambda: events.append(('listener', subject.get_state().value)))
        subject.dispatch(Add())
        self.assertEqual(events, [('a', 'pre', 0), ('b', 'pre', 0), ('listener', 1),
                                  ('b', 'post', 1), ('a', 'post', 1)])

    def test_each_phase_alone_and_unmatched_forwarding(self):
        for decorator in (intercept_pre, intercept_post):
            seen = []

            class Only(Middleware[State, Actions]):
                @decorator
                def handle(self, action: Add, ctx: API) -> None:
                    seen.append(ctx.get_state().value)

            subject = store(Only())
            subject.dispatch(Ignore())
            self.assertEqual(seen, [])
            subject.dispatch(Add())
            self.assertEqual(seen, [0 if decorator is intercept_pre else 1])
            self.assertEqual(subject.get_state(), State(1))

    def test_post_runs_after_consumption_with_original_action(self):
        seen = []

        class Post(Middleware[State, Actions]):
            @intercept_post
            def after(self, action: Add, ctx: API) -> None:
                seen.append((action, ctx.get_state()))

        class Consume(Middleware[State, Actions]):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                pass

        subject = store(Post(), Consume())
        action = Add()
        subject.dispatch(action)
        self.assertEqual(seen, [(action, State())])

    def test_post_receives_original_action_after_replacement(self):
        seen = []

        class Post(Middleware[State, Actions]):
            @intercept_post
            def after(self, action: Add, ctx: API) -> None:
                seen.append(action.amount)

        class Replace(Middleware[State, Actions]):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                ctx.next(Add(9))

        subject = store(Post(), Replace())
        subject.dispatch(Add())
        self.assertEqual(seen, [1])
        self.assertEqual(subject.get_state(), State(9))

    def test_pre_error_stops_forwarding_and_post(self):
        failure = ValueError('pre failed')
        seen = []

        class Fail(Middleware[State, Actions]):
            @intercept_pre
            def before(self, action: Add, ctx: API) -> None:
                raise failure

            @intercept_post
            def after(self, action: Add, ctx: API) -> None:
                seen.append(True)

        subject = store(Fail())
        with self.assertRaises(ValueError) as caught:
            subject.dispatch(Add())
        self.assertIs(caught.exception, failure)
        self.assertEqual(subject.get_state(), State())
        self.assertEqual(seen, [])

    def test_downstream_errors_skip_post_even_after_commit(self):
        for failing_listener in (False, True):
            seen = []
            failure = ValueError('downstream')

            class Post(Middleware[State, Actions]):
                @intercept_post
                def after(self, action: Add, ctx: API) -> None:
                    seen.append(True)

            def fail(*args):
                raise failure

            subject = store(Post()) if failing_listener else store(Post(), reducer=fail)
            if failing_listener:
                subject.subscribe(fail)
            with self.assertRaises(ValueError) as caught:
                subject.dispatch(Add())
            self.assertIs(caught.exception, failure)
            self.assertEqual(seen, [])
            self.assertEqual(subject.get_state(), State(1 if failing_listener else 0))

    def test_post_failure_does_not_roll_back(self):
        class Fail(Middleware[State, Actions]):
            @intercept_post
            def after(self, action: Add, ctx: API) -> None:
                raise ValueError('post')

        subject = store(Fail())
        with self.assertRaises(ValueError):
            subject.dispatch(Add())
        self.assertEqual(subject.get_state(), State(1))

    def test_post_can_dispatch_through_whole_chain(self):
        seen = []

        class Phases(Middleware[State, Actions]):
            @intercept_pre
            def before(self, action: Actions, ctx: API) -> None:
                seen.append(type(action))

            @intercept_post
            def after(self, action: Add, ctx: API) -> None:
                ctx.dispatch(Ignore())

        subject = store(Phases())
        subject.dispatch(Add())
        self.assertEqual(seen, [Add, Ignore])

    def test_phase_duplicates_and_manual_conflicts_rejected(self):
        for first, second in ((intercept_pre, intercept_pre), (intercept_post, intercept_post),
                              (intercept, intercept_pre), (intercept_post, intercept)):
            with self.subTest(first=first, second=second), self.assertRaises(DefinitionError):
                # Generate annotations appropriate to each decorator.
                namespace = dict(globals(), first=first, second=second)
                one = 'Context' if first is intercept else 'API'
                two = 'Context' if second is intercept else 'API'
                exec(f'''class Bad(Middleware[State, Actions]):
    @first
    def one(self, action: Add, ctx: {one}) -> None: pass
    @second
    def two(self, action: Add, ctx: {two}) -> None: pass
''', namespace)

    def test_runtime_overlap_fails_before_any_effect(self):
        seen = []

        class Overlap(Middleware[State, Actions]):
            @intercept_pre
            def before(self, action: BaseAction, ctx: API) -> None:
                seen.append('pre')

            @intercept
            def manual(self, action: Add, ctx: Context) -> None:
                seen.append('manual')

        with self.assertRaises(AmbiguousHandlerError):
            store(Overlap()).dispatch(Add())
        self.assertEqual(seen, [])

        class PostOverlap(Middleware[State, Actions]):
            @intercept_pre
            def before(self, action: Add, ctx: API) -> None:
                seen.append('pre')

            @intercept_post
            def broad(self, action: BaseAction, ctx: API) -> None:
                pass

            @intercept_post
            def specific(self, action: Add, ctx: API) -> None:
                pass

        with self.assertRaises(AmbiguousHandlerError):
            store(PostOverlap()).dispatch(Add())
        self.assertEqual(seen, [])

    def test_wrong_context_stacked_decorators_and_bad_results(self):
        for decorator in (intercept_pre, intercept_post):
            with self.assertRaises(DefinitionError):
                class Wrong(Middleware[State, Actions]):
                    @decorator
                    def handle(self, action: Add, ctx: Context) -> None:
                        pass

            class BadResult(Middleware[State, Actions]):
                @decorator
                def handle(self, action: Add, ctx: API) -> None:
                    return 123

            with self.assertRaises(TypeError):
                store(BadResult()).dispatch(Add())

        with self.assertRaises(DefinitionError):
            class Stacked(Middleware[State, Actions]):
                @intercept_pre
                @intercept_post
                def handle(self, action: Add, ctx: API) -> None:
                    pass

    def test_inherited_phase_super_and_direct_calls(self):
        seen = []

        class Parent(Middleware[State, Actions]):
            @intercept_pre
            def before(self, action: Add, ctx: API) -> None:
                seen.append('parent')

        class Child(Parent):
            @intercept_pre
            def before(self, action: Add, ctx: API) -> None:
                seen.append('child')
                super().before(action, ctx)

        subject = store(Child())
        subject.dispatch(Add())
        self.assertEqual(seen, ['child', 'parent'])
        api = API(subject.get_state, subject.dispatch)
        Child().before(action=Add(), ctx=api)
        self.assertEqual(subject.get_state(), State(1))  # Direct calls do not forward.
        with self.assertRaises(TypeError):
            Child().before(action=Ignore(), ctx=api)
