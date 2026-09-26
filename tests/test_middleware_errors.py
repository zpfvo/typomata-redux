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

            class Outer(Middleware[State, Actions], post_actions=Add):
                @intercept_post
                def after(self, action: Add, ctx: API) -> None:
                    seen.append(ctx.get_state().value)

            contracts = {"pre_actions" if decorator is intercept_pre else "post_actions": Add}
            class Fail(Middleware[State, Actions], **contracts):
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
            class Fail(Middleware[State, Actions], manual_actions=Add):
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
        class Consume(Middleware[State, Actions], manual_actions=Add):
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

            class Outer(Middleware[State, Actions], post_actions=Add):
                @intercept_post
                def after(self, action: Add, ctx: API) -> None:
                    seen.append('outer')

            class Cancel(Middleware[State, Actions], pre_actions=Add, post_actions=Add):
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
            class Cancel(Middleware[State, Actions], manual_actions=Add):
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
            class Fail(Middleware[State, Actions], pre_actions=Add):
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

                class Forward(Middleware[State, Actions], manual_actions=Add):
                    @intercept(catch_exceptions=True)
                    def effect(self, action: Add, ctx: Context) -> None:
                        ctx.next(action)

                def fail(*args):
                    calls.append(True)
                    raise failure

                def fail_reducer(state: State, action: Actions) -> State:
                    fail()

                subject = store(Forward()) if subscriber else store(Forward(), reducer=fail_reducer)
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

                contracts = {"pre_actions" if decorator is intercept_pre else "post_actions": Add}
                class Fail(Middleware[State, Actions], **contracts):
                    @decorator(catch_exceptions=True)
                    def effect(self, action: Add, ctx: API) -> None:
                        try:
                            ctx.dispatch(Ignore())
                        except ValueError:
                            if translate is not None:
                                raise translate
                            raise

                def reducer(state: State, action: Actions) -> State:
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
        class Twice(Middleware[State, Actions], manual_actions=Add):
            @intercept(catch_exceptions=True)
            def effect(self, action: Add, ctx: Context) -> None:
                ctx.next(action)
                ctx.next(action)

        with self.assertNoLogs(LOGGER), self.assertRaises(DispatchError):
            store(Twice()).dispatch(Add())

        class BadReturn(Middleware[State, Actions], pre_actions=Add):
            @intercept_pre(catch_exceptions=True)
            def effect(self, action: Add, ctx: API) -> None:
                return 1

        with self.assertNoLogs(LOGGER), self.assertRaises(TypeError):
            store(BadReturn()).dispatch(Add())

    def test_direct_calls_and_explicit_false_do_not_recover(self):
        class Fail(Middleware[State, Actions], pre_actions=Add, post_actions=Add):
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

    def test_retained_and_direct_dispatch_failures_in_every_phase(self):
        for decorator in (intercept, intercept_pre, intercept_post):
            for callback_kind in ('retained', 'direct', 'current'):
                for failure_source in ('reducer', 'middleware', 'subscriber'):
                    with self.subTest(phase=decorator.__name__, callback=callback_kind,
                                      source=failure_source):
                        saved = []
                        calls = []
                        failure = ValueError('nested failure')

                        def effect(action, ctx):
                            if action.amount == 0:
                                saved.append(ctx.dispatch)
                                return
                            callback = {'retained': saved[0], 'direct': subject.dispatch,
                                        'current': ctx.dispatch}[callback_kind]
                            callback(Ignore())

                        trigger = effect_middleware(decorator, effect)

                        class Fail(Middleware[State, Actions], pre_actions=Ignore):
                            @intercept_pre
                            def before(self, action: Ignore, ctx: API) -> None:
                                if failure_source == 'middleware':
                                    calls.append('middleware')
                                    raise failure

                        def reducer(state: State, action: Actions) -> State:
                            calls.append(type(action).__name__)
                            if isinstance(action, Ignore) and failure_source == 'reducer':
                                raise failure
                            return State(state.value + (action.amount if isinstance(action, Add) else 10))

                        def listener():
                            if subject.get_state().value >= 10:
                                calls.append('subscriber')
                                raise failure

                        subject = store(trigger, Fail(), reducer=reducer)
                        subject.dispatch(Add(0))
                        calls.clear()
                        if failure_source == 'subscriber':
                            subject.subscribe(listener)
                        with self.assertNoLogs(LOGGER), self.assertRaises(ValueError) as caught:
                            subject.dispatch(Add())
                        self.assertIs(caught.exception, failure)
                        outer_reduced = decorator is intercept_post
                        expected_calls = ['Add'] if outer_reduced else []
                        expected_calls.append('middleware' if failure_source == 'middleware' else 'Ignore')
                        if failure_source == 'subscriber':
                            expected_calls.append('subscriber')
                        self.assertEqual(calls, expected_calls)
                        self.assertEqual(subject.get_state(), State(
                            int(outer_reduced) + (10 if failure_source == 'subscriber' else 0)))

    def test_retained_dispatch_translated_error_and_cancellation_propagate(self):
        for translated in (ValueError('translated'), CancelAction()):
            saved = []
            original = ValueError('reducer')

            def effect(action, ctx):
                if action.amount == 0:
                    saved.append(ctx.dispatch)
                    return
                try:
                    saved[0](Ignore())
                except ValueError as error:
                    raise translated from error

            def reducer(state: State, action: Actions) -> State:
                if isinstance(action, Ignore):
                    raise original
                return state

            subject = store(effect_middleware(intercept_pre, effect), reducer=reducer)
            subject.dispatch(Add(0))
            with self.assertNoLogs(LOGGER), self.assertRaises(type(translated)) as caught:
                subject.dispatch(Add())
            self.assertIs(caught.exception, translated)
            self.assertIs(caught.exception.__cause__, original)

    def test_failure_in_another_store_protects_current_handler(self):
        failure = ValueError('other store reducer')

        def fail(state: State, action: Actions) -> State:
            raise failure

        other = store(reducer=fail)

        def effect(action, ctx):
            other.dispatch(Ignore())

        subject = store(effect_middleware(intercept_pre, effect))
        with self.assertNoLogs(LOGGER), self.assertRaises(ValueError) as caught:
            subject.dispatch(Add())
        self.assertIs(caught.exception, failure)
        self.assertEqual(subject.get_state(), State())
        self.assertEqual(other.get_state(), State())

    def test_explicitly_handled_nested_failure_does_not_taint_outer(self):
        # The nested handler calls a callback created by the still-active outer
        # handler. Only the nested caller should be marked when that call fails.
        saved = []
        nested_failure = ValueError('nested')
        outer_failure = ValueError('outer effect')

        class Handle(Middleware[State, Actions], pre_actions=Add):
            @intercept_pre(catch_exceptions=True)
            def before(self, action: Add, ctx: API) -> None:
                if action.amount == 1:
                    saved.append(ctx.dispatch)
                    ctx.dispatch(Add(2))
                    raise outer_failure
                try:
                    saved[0](Ignore())
                except ValueError:
                    pass

        def reducer(state: State, action: Actions) -> State:
            if isinstance(action, Ignore):
                raise nested_failure
            return State(state.value + action.amount)

        subject = store(Handle(), reducer=reducer)
        with self.assertLogs(LOGGER) as logs:
            subject.dispatch(Add())
        self.assertEqual(subject.get_state(), State(3))
        self.assertEqual(len(logs.records), 1)
        self.assertIs(logs.records[0].exc_info[1], outer_failure)

    def test_nested_recovery_keeps_outer_recovery_enabled(self):
        failure = ValueError('effect')

        def effect(action, ctx):
            if action.amount == 1:
                ctx.dispatch(Add(2))
            raise failure

        subject = store(effect_middleware(intercept_pre, effect))
        with self.assertLogs(LOGGER) as logs:
            subject.dispatch(Add())
        self.assertEqual(subject.get_state(), State(3))
        self.assertEqual(len(logs.records), 2)

    def test_scope_cleanup_and_reused_exception_after_failure(self):
        failure = ValueError('same exception object')
        saved = []

        def effect(action, ctx):
            if action.amount == 0:
                saved.append(ctx.dispatch)
            elif action.amount == 1:
                saved[0](Ignore())
            else:
                raise failure

        def reducer(state: State, action: Actions) -> State:
            if isinstance(action, Ignore):
                raise failure
            return State(state.value + action.amount)

        subject = store(effect_middleware(intercept_pre, effect), reducer=reducer)
        subject.dispatch(Add(0))
        with self.assertNoLogs(LOGGER), self.assertRaises(ValueError):
            subject.dispatch(Add())
        with self.assertLogs(LOGGER):
            subject.dispatch(Add(2))
        self.assertEqual(subject.get_state(), State(2))
        # The retained callback also propagates normally outside any handler.
        with self.assertNoLogs(LOGGER), self.assertRaises(ValueError) as caught:
            saved[0](Ignore())
        self.assertIs(caught.exception, failure)
        other = store(effect_middleware(intercept_pre, effect))
        with self.assertLogs(LOGGER):
            other.dispatch(Add(3))
        self.assertEqual(other.get_state(), State(3))


def effect_middleware(decorator, effect):
    """Run the same effect with the context appropriate to each handler phase."""
    if decorator is intercept:
        class Manual(Middleware[State, Actions], manual_actions=Add):
            @intercept(catch_exceptions=True)
            def handle(self, action: Add, ctx: Context) -> None:
                effect(action, ctx)
                ctx.next(action)
        return Manual()

    contracts = {"pre_actions" if decorator is intercept_pre else "post_actions": Add}
    class Automatic(Middleware[State, Actions], **contracts):
        @decorator(catch_exceptions=True)
        def handle(self, action: Add, ctx: API) -> None:
            effect(action, ctx)
    return Automatic()
