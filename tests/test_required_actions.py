"""Construction checks for application actions that require reducer registrations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Protocol, TypeVar
import unittest

from typomata_redux import (
    CombinedReducer, DefinitionError, FunctionReducer, Middleware, MiddlewareContext,
    Store, StoreAPI, combine_reducers, intercept, intercept_post,
)
from typomata_redux._inspection import describe_store


class Add:
    pass


class Reset:
    pass


class NoOp:
    pass


class Track:
    pass


class SpecialAdd(Add):
    pass


Actions = Add | Reset | NoOp | Track


@dataclass(frozen=True)
class Count:
    value: int = 0


@dataclass(frozen=True)
class Pair:
    first: Count = Count()
    second: Count = Count()


@dataclass(frozen=True)
class Root:
    pair: Pair = Pair()


def increment(state: Count, action: Add) -> Count:
    return Count(state.value + 1)


def reset(state: Count, action: Reset) -> Count:
    return Count()


class RequiredActionTests(unittest.TestCase):
    def test_missing_root_registration_fails_before_factories_or_reducers_run(self):
        def never(state: Count, action: Add) -> Count:
            raise AssertionError('must not run')

        def factory(api, next_dispatch):
            raise AssertionError('must not run')

        with self.assertRaisesRegex(DefinitionError, 'missing reducer.*Reset, NoOp'):
            Store[Count, Actions](initial_state=Count(), reducer=never,
                                  required_actions=Add | Reset | NoOp, middleware=[factory])

    def test_nested_composition_covers_actions_across_different_slices(self):
        reducer = combine_reducers(Root, pair=combine_reducers(Pair, first=increment, second=reset))
        subject = Store[Root, Actions](initial_state=Root(), reducer=reducer,
                                       required_actions=Add | Reset)
        subject.dispatch(Add())
        self.assertEqual(subject.get_state().pair.first, Count(1))
        self.assertEqual(describe_store(subject).required_actions, (Add, Reset))
        missing = combine_reducers(Root, pair=combine_reducers(Pair, first=increment))
        with self.assertRaisesRegex(DefinitionError, 'missing reducer.*Reset'):
            Store[Root, Actions](initial_state=Root(), reducer=missing, required_actions=Add | Reset)

    def test_shared_actions_need_at_least_one_registration(self):
        reducer = combine_reducers(Pair, first=increment, second=increment)
        subject = Store[Pair, Add](initial_state=Pair(), reducer=reducer, required_actions=Add)
        subject.dispatch(Add())
        self.assertEqual(subject.get_state(), Pair(Count(1), Count(1)))

    def test_explicit_noop_is_covered_without_changing_identity(self):
        def count(state: Count, action: Add | NoOp) -> Count:
            if isinstance(action, NoOp):
                return state
            return increment(state, action)

        subject = Store[Count, Actions](initial_state=Count(), reducer=count,
                                       required_actions=Add | NoOp)
        initial = subject.get_state()
        subject.dispatch(NoOp())
        self.assertIs(subject.get_state(), initial)

    def test_effect_only_actions_can_be_excluded_from_reducer_contract(self):
        seen = []

        class Tracking(Middleware[Count, Actions], post_actions=Track):
            @intercept_post
            def track(self, action: Track, ctx: StoreAPI[Count, Actions]) -> None:
                seen.append(ctx.get_state())

        subject = Store[Count, Actions](initial_state=Count(), reducer=increment,
                                       required_actions=Add, middleware=[Tracking()])
        subject.dispatch(Track())
        self.assertEqual(seen, [Count()])
        self.assertEqual(subject.get_state(), Count())

    def test_middleware_does_not_count_as_reducer_coverage(self):
        class Tracking(Middleware[Count, Actions], post_actions=Track):
            @intercept_post
            def track(self, action: Track, ctx: StoreAPI[Count, Actions]) -> None:
                pass

        with self.assertRaisesRegex(DefinitionError, 'missing reducer.*Track'):
            Store[Count, Actions](initial_state=Count(), reducer=increment,
                                  required_actions=Add | Track, middleware=[Tracking()])

    def test_coverage_does_not_promise_execution_past_consuming_middleware(self):
        class Consume(Middleware[Count, Actions], manual_actions=Add):
            @intercept
            def consume(self, action: Add, ctx: MiddlewareContext[Count, Actions]) -> None:
                pass

        subject = Store[Count, Actions](initial_state=Count(), reducer=increment,
                                       required_actions=Add, middleware=[Consume()])
        subject.dispatch(Add())
        self.assertEqual(subject.get_state(), Count())

    def test_superclasses_cover_subclasses_but_not_the_reverse(self):
        Store[Count, SpecialAdd](initial_state=Count(), reducer=increment, required_actions=SpecialAdd)

        def special(state: Count, action: SpecialAdd) -> Count:
            return state

        with self.assertRaisesRegex(DefinitionError, 'missing reducer.*Add'):
            Store[Count, Add](initial_state=Count(), reducer=special, required_actions=Add)

    def test_broad_object_reducer_covers_all_declared_actions(self):
        def all_actions(state: Count, action: object) -> Count:
            return state

        Store[Count, Actions](initial_state=Count(), reducer=all_actions, required_actions=Actions)
        with self.assertRaisesRegex(DefinitionError, 'missing reducer.*object'):
            Store[Count, object](initial_state=Count(), reducer=increment, required_actions=object)

    def test_empty_composition_cannot_cover_a_required_action(self):
        with self.assertRaisesRegex(DefinitionError, 'missing reducer.*Add'):
            Store[Pair, Add](initial_state=Pair(), reducer=combine_reducers(Pair), required_actions=Add)
        Store[Pair, Add](initial_state=Pair(), reducer=combine_reducers(Pair))

    def test_annotation_normalization_and_snapshot(self):
        contract = Annotated[Add | SpecialAdd, 'required']
        subject = Store[Count, Actions](initial_state=Count(), reducer=increment,
                                       required_actions=contract)
        contract = Add | Reset
        self.assertEqual(describe_store(subject).required_actions, (Add, SpecialAdd))
        with self.assertRaisesRegex(DefinitionError, 'missing reducer.*Reset'):
            Store[Count, Actions](initial_state=Count(), reducer=increment, required_actions=contract)

    def test_invalid_required_action_annotations_fail_at_construction(self):
        class Structural(Protocol):
            pass

        for invalid in (Any, list[Add], TypeVar('T'), Structural, (Add,), 'Add', False):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(DefinitionError, 'required_actions: expected concrete classes'):
                    Store(initial_state=Count(), reducer=increment, required_actions=invalid)

    def test_custom_dispatch_cannot_claim_coverage_from_unused_metadata(self):
        class CustomFunction(FunctionReducer[Count]):
            def __call__(self, state, action):
                return state

        class CustomCombined(CombinedReducer[Pair]):
            def __call__(self, state, action):
                return state

        for initial, reducer in ((Count(), CustomFunction(increment)),
                                 (Pair(), CustomCombined(Pair, first=increment)),
                                 (Pair(), combine_reducers(Pair, first=CustomFunction(increment)))):
            with self.subTest(reducer=reducer):
                with self.assertRaisesRegex(DefinitionError, 'cannot verify.*custom __call__'):
                    Store(initial_state=initial, reducer=reducer, required_actions=Add)
