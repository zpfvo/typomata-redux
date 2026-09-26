"""Annotated function reducers with ordinary Python state and action types."""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Annotated, Any, Protocol, TypeVar
import unittest

from typomata_redux import (
    CancelAction, DefinitionError, FunctionReducer, Middleware, Store, StoreAPI,
    combine_reducers, intercept_post, intercept_pre,
)
from typomata_redux._inspection import describe_reducer, describe_store


@dataclass(frozen=True)
class Count:
    value: int = 0


@dataclass(frozen=True)
class Add:
    amount: int = 1


class Reset:
    pass


class Remember:
    pass


class NoOp:
    pass


@dataclass(frozen=True)
class History:
    values: tuple[int, ...] = ()


@dataclass(frozen=True)
class Root:
    count: Count = Count()
    history: History = History()


@dataclass(frozen=True)
class Nested:
    feature: Root = Root()


Actions = Add | Reset | Remember | NoOp
API = StoreAPI[Root, Actions]
T = TypeVar('T')


def counter(state: Count, action: Add | Reset) -> Count:
    if isinstance(action, Add):
        return Count(state.value + action.amount)
    return Count()


def recorder(state: History, action: Remember | Reset) -> History:
    if isinstance(action, Reset):
        return History()
    return History((*state.values, 1))


class FunctionTests(unittest.TestCase):
    def test_narrow_functions_route_shared_actions_and_preserve_root_identity(self):
        combined = combine_reducers(Root, count=counter, history=recorder)
        subject = Store[Root, Actions](initial_state=Root(), reducer=combined)
        original = subject.get_state()
        subject.dispatch(NoOp())
        self.assertIs(subject.get_state(), original)
        subject.dispatch(Add(3))
        self.assertEqual(subject.get_state(), Root(Count(3)))
        self.assertIs(subject.get_state().history, original.history)
        subject.dispatch(Remember())
        self.assertEqual(subject.get_state(), Root(Count(3), History((1,))))
        subject.dispatch(Reset())
        self.assertEqual(subject.get_state(), Root())
        self.assertEqual(combine_reducers(Nested, feature=combined)(Nested(), Add()).feature.count, Count(1))

    def test_unrelated_action_never_calls_function(self):
        calls = []
        def only_add(state: Count, action: Add) -> Count:
            calls.append(action)
            return Count(1)
        reducer = FunctionReducer(only_add)
        original = Count()
        self.assertIs(reducer(original, Reset()), original)
        self.assertEqual(calls, [])
        class Child(Add):
            pass
        self.assertEqual(reducer(original, Child()), Count(1))
        self.assertEqual(len(calls), 1)

    def test_original_function_signature_and_keyword_names_remain_usable(self):
        reducer = FunctionReducer(counter)
        self.assertEqual(counter(state=Count(1), action=Add(2)), Count(3))
        self.assertEqual(reducer(Count(1), Add(2)), Count(3))
        with self.assertRaises(AttributeError):
            # Original functions use ordinary Python semantics; the adapter owns
            # runtime annotation checking and no-op routing.
            counter(object(), Add())

    def test_bound_method_annotated_union_and_positional_only(self):
        class Service:
            Action = Add | Reset
            def __init__(self, offset):
                self.offset = offset
            def reduce(self, state: Count, event: Annotated[Action, 'route'], /) -> Count:
                return Count(self.offset) if isinstance(event, Reset) else Count(state.value + event.amount)
        reducer = combine_reducers(Root, count=Service(9).reduce)
        self.assertEqual(reducer(Root(), Reset()).count, Count(9))
        self.assertEqual(reducer(Root(), Add(2)).count, Count(2))

    def test_annotations_are_snapshotted_and_visible_to_inspection(self):
        def effect(state: Count, action: Add) -> Count:
            return Count(2)
        reducer = FunctionReducer(effect)
        effect.__annotations__['action'] = Reset
        info = describe_reducer(reducer)
        self.assertEqual(info.kind, 'function')
        self.assertEqual(info.transitions[0].actions, (Add,))
        self.assertEqual(info.transitions[0].sources, (Count,))
        self.assertEqual(info.transitions[0].destinations, (Count,))
        self.assertEqual(reducer(Count(), Add()), Count(2))
        before = Count()
        self.assertIs(reducer(before, Reset()), before)
        combined = combine_reducers(Root, count=reducer)
        self.assertEqual(describe_reducer(combined).fields[0].reducer, info)

    def test_wrong_state_and_declared_result_fail_at_composition(self):
        with self.assertRaisesRegex(DefinitionError, 'Root.count'):
            combine_reducers(Root, count=recorder)
        def wrong_result(state: Count, action: Add) -> History:
            return History()
        with self.assertRaisesRegex(DefinitionError, 'Root.count.*result'):
            combine_reducers(Root, count=wrong_result)

    def test_function_must_accept_all_field_state_variants(self):
        @dataclass(frozen=True)
        class Variant:
            value: Count | History = Count()
        with self.assertRaisesRegex(DefinitionError, 'every declared field state'):
            combine_reducers(Variant, value=counter)
        def handle(state: Count | History, action: Reset) -> Count | History:
            return Count() if isinstance(state, Count) else History()
        self.assertEqual(combine_reducers(Variant, value=handle)(Variant(History((1,))), Reset()), Variant(History()))

    def test_failure_and_lying_return_annotation_never_commit(self):
        failure = TypeError('body failure')
        def fail(state: Count, action: Add) -> Count:
            raise failure
        def wrong(state: Count, action: Add) -> Count:
            return History()
        for function in (fail, wrong):
            subject = Store[Root, Actions](initial_state=Root(), reducer=combine_reducers(Root, count=function))
            original = subject.get_state()
            with self.assertRaises(TypeError) as caught:
                subject.dispatch(Add())
            if function is fail:
                self.assertIs(caught.exception, failure)
            self.assertIs(subject.get_state(), original)

    def test_standalone_adapter_checks_state_and_lazy_results(self):
        reducer = FunctionReducer(counter)
        with self.assertRaisesRegex(TypeError, 'state'):
            reducer(History(), NoOp())
        async def later():
            return Count()
        def wrong(state: Count, action: Add) -> Count:
            return later()
        subject = Store[Count, Add](initial_state=Count(), reducer=FunctionReducer(wrong))
        with self.assertRaisesRegex(TypeError, 'coroutine'):
            subject.dispatch(Add())
        self.assertEqual(subject.get_state(), Count())
        subject = Store[Count, Add](initial_state=Count(), reducer=wrong)
        with self.assertRaisesRegex(TypeError, 'coroutine'):
            subject.dispatch(Add())
        self.assertEqual(subject.get_state(), Count())

        def generated():
            yield Count()
        class Awaitable:
            def __await__(self):
                yield Count()
        for value in (generated(), Awaitable()):
            subject = Store[Count, Add](initial_state=Count(), reducer=lambda state, action: value)
            with self.assertRaisesRegex(TypeError, 'synchronous reducer'):
                subject.dispatch(Add())
            self.assertEqual(subject.get_state(), Count())

    def test_missing_annotations_and_unsupported_callables_fail_early(self):
        async def asynchronous(state: Count, action: Add) -> Count:
            return state
        def generator(state: Count, action: Add) -> Count:
            yield state
        def keyword_only(state: Count, *, action: Add) -> Count:
            return state
        def defaulted(state: Count, action: Add = Add()) -> Count:
            return state
        class CallableReducer:
            def __call__(self, state: Count, action: Add) -> Count:
                return state
        for function in (lambda state, action: state, asynchronous, generator,
                         keyword_only, defaulted, CallableReducer(), partial(counter)):
            with self.subTest(function=function), self.assertRaises(DefinitionError):
                combine_reducers(Root, count=function)

    def test_unsupported_annotations_and_unresolved_names_fail_early(self):
        class ActionProtocol(Protocol):
            amount: int
        for annotation in (Any, T, list[Add], ActionProtocol, 'MissingAction'):
            def function(state: Count, action: Add) -> Count:
                return state
            function.__annotations__['action'] = annotation
            with self.subTest(annotation=annotation), self.assertRaises(DefinitionError):
                FunctionReducer(function)
        def missing(state: Count, action: Add):
            return state
        with self.assertRaises(DefinitionError):
            FunctionReducer(missing)

    def test_scalar_state_none_and_broad_object_actions(self):
        @dataclass(frozen=True)
        class Scalar:
            value: int | None = None
        def count(state: int | None, action: object) -> int | None:
            return (state or 0) + 1
        reducer = combine_reducers(Scalar, value=count)
        self.assertEqual(reducer(Scalar(), 'anything'), Scalar(1))
        subject = Store[int, str](initial_state=0, reducer=lambda state, action: state + len(action))
        subject.dispatch('add')
        self.assertEqual(subject.get_state(), 3)

    def test_plain_actions_and_injected_middleware_work_without_markers(self):
        records = []
        class Validate(Middleware[Root, Actions], pre_actions=Add):
            @intercept_pre
            def before(self, action: Add, ctx: API) -> None:
                if action.amount < 0:
                    raise CancelAction()
        class Effect(Middleware[Root, Actions], post_actions=Add):
            def __init__(self, database):
                self.database = database
            @intercept_post
            def after(self, action: Add, ctx: API) -> None:
                self.database.append(ctx.get_state().count.value)
                ctx.dispatch(Remember())
        subject = Store[Root, Actions](initial_state=Root(),
            reducer=combine_reducers(Root, count=counter, history=recorder),
            middleware=[Validate(), Effect(records)])
        subject.dispatch(Add(-1))
        self.assertEqual(records, [])
        subject.dispatch(Add(4))
        self.assertEqual(records, [4])
        self.assertEqual(subject.get_state(), Root(Count(4), History((1,))))
        self.assertEqual(describe_store(subject).reducer.fields[0].reducer.kind, 'function')
