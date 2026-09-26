"""Stores and composition accept the same annotated reducer contract."""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any
import unittest

from typomata_redux import DefinitionError, Store, combine_reducers
from typomata_redux._inspection import describe_store


@dataclass(frozen=True)
class Count:
    value: int = 0


@dataclass(frozen=True)
class Done:
    total: int


@dataclass(frozen=True)
class Root:
    count: Count = Count()


class Add:
    pass


class Ignore:
    pass


class StoreReducerTests(unittest.TestCase):
    def test_plain_narrow_root_routes_actions_and_preserves_noop_identity(self):
        calls = []

        def count(state: Count, action: Add) -> Count:
            calls.append(action)
            return Count(state.value + 1)

        subject = Store[Count, Add | Ignore](initial_state=Count(), reducer=count)
        original = subject.get_state()
        subject.dispatch(Ignore())
        self.assertIs(subject.get_state(), original)
        self.assertEqual(calls, [])
        subject.dispatch(Add())
        self.assertEqual(subject.get_state(), Count(1))
        self.assertEqual(len(calls), 1)
        self.assertEqual(describe_store(subject).reducer.kind, 'function')

    def test_root_results_are_checked_before_commit_and_notification(self):
        def wrong(state: Count, action: Add) -> Count:
            return 'invalid'

        subject = Store[Count, Add](initial_state=Count(), reducer=wrong)
        original = subject.get_state()
        notified = []
        subject.subscribe(lambda: notified.append(True))
        with self.assertRaisesRegex(TypeError, 'result.*expected Count'):
            subject.dispatch(Add())
        self.assertIs(subject.get_state(), original)
        self.assertEqual(notified, [])

    def test_initial_state_is_checked_without_running_reducers_or_factories(self):
        def count(state: Count, action: Add) -> Count:
            raise AssertionError('must not run')

        def factory(api, next_dispatch):
            raise AssertionError('must not run')

        for initial, reducer in ((Done(0), count),
                                 (Root(Done(0)), combine_reducers(Root, count=count))):
            with self.subTest(initial=initial):
                with self.assertRaisesRegex(TypeError, 'expected Count'):
                    Store(initial_state=initial, reducer=reducer, middleware=[factory])

    def test_root_union_state_can_transition_and_continue(self):
        def finish(state: Count | Done, action: Add) -> Count | Done:
            if isinstance(state, Count):
                return Done(state.value)
            return state

        subject = Store[Count | Done, Add](initial_state=Count(4), reducer=finish)
        subject.dispatch(Add())
        finished = subject.get_state()
        self.assertEqual(finished, Done(4))
        subject.dispatch(Add())
        self.assertIs(subject.get_state(), finished)

    def test_bound_method_works_at_root_and_in_composition(self):
        class Logic:
            def __init__(self, amount):
                self.amount = amount

            def count(self, state: Count, action: Add) -> Count:
                return Count(state.value + self.amount)

        logic = Logic(3)
        root = Store[Count, Add](initial_state=Count(), reducer=logic.count)
        combined = Store[Root, Add](initial_state=Root(),
                                    reducer=combine_reducers(Root, count=logic.count))
        root.dispatch(Add())
        combined.dispatch(Add())
        self.assertEqual(root.get_state(), combined.get_state().count)

    def test_unsupported_reducers_fail_at_both_boundaries(self):
        async def asynchronous(state: Count, action: Add) -> Count:
            return state

        def generic(state: Any, action: Add) -> Any:
            return state

        def wrong_result(state: Count, action: Add) -> Done:
            return Done(0)

        def defaulted(state: Count, action: Add = Add()) -> Count:
            return state

        class CallableReducer:
            def __call__(self, state: Count, action: Add) -> Count:
                return state

        for reducer in (lambda state, action: state, asynchronous, generic,
                        wrong_result, defaulted, CallableReducer(), partial(defaulted)):
            with self.subTest(reducer=reducer):
                with self.assertRaises(DefinitionError):
                    Store(initial_state=Count(), reducer=reducer)
                with self.assertRaises(DefinitionError):
                    combine_reducers(Root, count=reducer)

    def test_root_annotations_are_snapshotted(self):
        def count(state: Count, action: Add) -> Count:
            return Count(state.value + 1)

        subject = Store[Count, Add | Ignore](initial_state=Count(), reducer=count)
        count.__annotations__['action'] = Ignore
        subject.dispatch(Ignore())
        self.assertEqual(subject.get_state(), Count())
        subject.dispatch(Add())
        self.assertEqual(subject.get_state(), Count(1))
