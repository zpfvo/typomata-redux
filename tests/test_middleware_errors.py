from __future__ import annotations

import unittest

from typomata_redux import (
    CancelAction, DefinitionError, DispatchError, Middleware, MiddlewareError,
    StoreAPI, intercept, intercept_post, intercept_pre,
)
from test_redux import Actions, Add, Context, Ignore, State, store

API = StoreAPI[State, Actions]
LOGGER = 'typomata_redux.middleware'


class ErrorTests(unittest.TestCase):
    def test_automatic_recovery_logs_traceback_and_continues(self):
        for decorator in (intercept_pre, intercept_post):
            failure = ValueError('effect failed')
            seen = []

            class Outer(Middleware[State, Actions]):
                @intercept_post
                def after(self, action: Add, ctx: API) -> None:
                    seen.append(ctx.get_state().value)

            class Fail(Middleware[State, Actions]):
                @decorator(catch_exceptions=True)
                def effect(self, action: Add, ctx: API) -> None:
                    raise failure

            subject = store(Outer(), Fail())
            with self.assertLogs(LOGGER) as logs:
                subject.dispatch(Add())
            self.assertEqual(seen, [1])
            self.assertEqual(len(logs.records), 1)
            self.assertIs(logs.records[0].exc_info[1], failure)
            self.assertIn('effect', logs.output[0])

    def test_manual_recovery_forwards_exactly_once(self):
        for forward_first in (False, True):
            class Fail(Middleware[State, Actions]):
                @intercept(catch_exceptions=True)
                def effect(self, action: Add, ctx: Context) -> None:
                    if forward_first:
                        ctx.next(Add(5))
                    raise ValueError('effect')

            subject = store(Fail())
            notifications = []
            subject.subscribe(lambda: notifications.append(True))
            with self.assertLogs(LOGGER):
                subject.dispatch(Add())
            self.assertEqual(subject.get_state(), State(5 if forward_first else 1))
            self.assertEqual(notifications, [True])

    def test_successful_consumption_is_not_recovered(self):
        class Consume(Middleware[State, Actions]):
            @intercept(catch_exceptions=True)
            def effect(self, action: Add, ctx: Context) -> None:
                pass

        subject = store(Consume())
        with self.assertNoLogs(LOGGER):
            subject.dispatch(Add())
        self.assertEqual(subject.get_state(), State())

    def test_cancel_pre_skips_own_post_but_unwinds_outer(self):
        for catch in (False, True):
            seen = []

            class Outer(Middleware[State, Actions]):
                @intercept_post
                def after(self, action: Add, ctx: API) -> None:
                    seen.append('outer')

            class Cancel(Middleware[State, Actions]):
                @intercept_pre(catch_exceptions=catch)
                def before(self, action: Add, ctx: API) -> None:
                    raise CancelAction()

                @intercept_post
                def after(self, action: Add, ctx: API) -> None:
                    seen.append('inner')

            subject = store(Outer(), Cancel())
            with self.assertNoLogs(LOGGER):
                subject.dispatch(Add())
            self.assertEqual(seen, ['outer'])
            self.assertEqual(subject.get_state(), State())

    def test_manual_cancel_before_and_after_next(self):
        for forward_first in (False, True):
            class Cancel(Middleware[State, Actions]):
                @intercept(catch_exceptions=True)
                def effect(self, action: Add, ctx: Context) -> None:
                    if forward_first:
                        ctx.next(action)
                    raise CancelAction()

            subject = store(Cancel())
            subject.dispatch(Add())
            self.assertEqual(subject.get_state(), State(int(forward_first)))

    def test_fatal_and_base_exceptions_propagate(self):
        for failure in (MiddlewareError('fatal'), KeyboardInterrupt(), SystemExit()):
            class Fail(Middleware[State, Actions]):
                @intercept_pre(catch_exceptions=True)
                def before(self, action: Add, ctx: API) -> None:
                    raise failure

            subject = store(Fail())
            with self.assertNoLogs(LOGGER), self.assertRaises(type(failure)) as caught:
                subject.dispatch(Add())
            self.assertIs(caught.exception, failure)
            self.assertEqual(subject.get_state(), State())

    def test_downstream_failure_is_never_recovered_or_retried(self):
        for subscriber in (False, True):
            for failure in (ValueError('downstream'), CancelAction()):
                calls = []

                class Forward(Middleware[State, Actions]):
                    @intercept(catch_exceptions=True)
                    def effect(self, action: Add, ctx: Context) -> None:
                        ctx.next(action)

                def fail(*args):
                    calls.append(True)
                    raise failure

                subject = store(Forward()) if subscriber else store(Forward(), reducer=fail)
                if subscriber:
                    subject.subscribe(fail)
                with self.assertNoLogs(LOGGER), self.assertRaises(type(failure)) as caught:
                    subject.dispatch(Add())
                self.assertIs(caught.exception, failure)
                self.assertEqual(calls, [True])
                self.assertEqual(subject.get_state(), State(int(subscriber)))

    def test_nested_dispatch_and_translated_failures_propagate(self):
        for decorator in (intercept_pre, intercept_post):
            for translate in (None, ValueError('translated'), CancelAction()):
                original = ValueError('nested')

                class Fail(Middleware[State, Actions]):
                    @decorator(catch_exceptions=True)
                    def effect(self, action: Add, ctx: API) -> None:
                        try:
                            ctx.dispatch(Ignore())
                        except ValueError:
                            if translate is not None:
                                raise translate
                            raise

                def reducer(state, action):
                    if isinstance(action, Ignore):
                        raise original
                    return State(state.value + action.amount)

                subject = store(Fail(), reducer=reducer)
                expected = original if translate is None else translate
                with self.assertNoLogs(LOGGER), self.assertRaises(type(expected)) as caught:
                    subject.dispatch(Add())
                self.assertIs(caught.exception, expected)
                self.assertEqual(subject.get_state(), State(int(decorator is intercept_post)))

    def test_recovery_does_not_hide_contract_errors(self):
        class Twice(Middleware[State, Actions]):
            @intercept(catch_exceptions=True)
            def effect(self, action: Add, ctx: Context) -> None:
                ctx.next(action)
                ctx.next(action)

        with self.assertNoLogs(LOGGER), self.assertRaises(DispatchError):
            store(Twice()).dispatch(Add())

        class BadReturn(Middleware[State, Actions]):
            @intercept_pre(catch_exceptions=True)
            def effect(self, action: Add, ctx: API) -> None:
                return 1

        with self.assertNoLogs(LOGGER), self.assertRaises(TypeError):
            store(BadReturn()).dispatch(Add())

    def test_direct_calls_and_explicit_false_do_not_recover(self):
        class Fail(Middleware[State, Actions]):
            @intercept_pre(catch_exceptions=True)
            def before(self, action: Add, ctx: API) -> None:
                raise ValueError('direct')

            @intercept_post(catch_exceptions=False)
            def after(self, action: Add, ctx: API) -> None:
                raise ValueError('post')

        subject = store(Fail())
        with self.assertNoLogs(LOGGER), self.assertRaisesRegex(ValueError, 'direct'):
            Fail().before(Add(), StoreAPI(subject.get_state, subject.dispatch))
        with self.assertLogs(LOGGER), self.assertRaisesRegex(ValueError, 'post'):
            subject.dispatch(Add())
        self.assertEqual(subject.get_state(), State(1))

    def test_invalid_configuration(self):
        for decorator in (intercept, intercept_pre, intercept_post):
            with self.assertRaises(DefinitionError):
                decorator(catch_exceptions='yes')
