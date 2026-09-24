from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
import unittest

from typomata import BaseAction, BaseState, BaseStateMachine, transition
from typomata_redux import (
    AmbiguousHandlerError, CombinedReducer, MachineReducer, Middleware, Store,
    StoreAPI, combine_reducers, intercept, intercept_post, intercept_pre,
)
from typomata_redux._inspection import describe_middleware, describe_reducer, describe_store
from test_redux import Actions, Add, Context, Finished, Ignore, State, adapter, store

API = StoreAPI[State, Actions]


@dataclass(frozen=True)
class Pair(BaseState):
    left: State = State()
    right: State = State()
    label: str = 'retained'


@dataclass(frozen=True)
class Root(BaseState):
    pair: Pair = Pair()
    count: State = State()


class InspectionTests(unittest.TestCase):
    def test_store_order_duplicates_and_inspection_without_execution(self):
        calls = []

        class Observe(Middleware[State, Actions]):
            @intercept_pre(catch_exceptions=True)
            def before(self, action: Add, ctx: API) -> None:
                calls.append('pre')

            @intercept_post
            def after(self, action: Actions, ctx: API) -> None:
                calls.append('post')

        def plain(api, next_dispatch):
            calls.append('factory')
            return next_dispatch

        def reducer(state, action):
            calls.append('reducer')
            return state

        observe = Observe()
        subject = store(observe, plain, observe, reducer=reducer)
        subject.subscribe(lambda: calls.append('subscriber'))
        original = subject.get_state()
        info = describe_store(subject)
        self.assertEqual(calls, ['factory'])
        self.assertIs(subject.get_state(), original)
        self.assertEqual([item.kind for item in info.middleware], ['annotated', 'opaque', 'annotated'])
        self.assertEqual(info.middleware[0], info.middleware[2])
        self.assertTrue(info.middleware[1].name.endswith('.plain'))
        self.assertEqual(info.middleware[1].handlers, ())
        self.assertEqual(info.reducer.kind, 'opaque')
        handlers = info.middleware[0].handlers
        self.assertEqual([item.phase for item in handlers], ['pre', 'post'])
        self.assertEqual([item.actions for item in handlers], [(Add,), (Add, Ignore)])
        self.assertEqual([item.catch_exceptions for item in handlers], [True, False])
        subject.dispatch(Add())
        self.assertEqual(calls, ['factory', 'pre', 'pre', 'reducer', 'subscriber', 'post', 'post'])

    def test_composition_keeps_paths_order_repeated_children_and_function_fields(self):
        child = adapter()
        pair = combine_reducers(Pair, right=child, left=child)

        def plain(state: State, action: Add) -> State:
            return state

        reducer = combine_reducers(Root, pair=pair, count=plain)
        info = describe_reducer(reducer)
        self.assertEqual(info.kind, 'combined')
        self.assertIs(info.state_type, Root)
        self.assertEqual([item.name for item in info.fields], ['pair', 'count'])
        nested = info.fields[0].reducer
        self.assertIs(nested.state_type, Pair)
        self.assertEqual([item.name for item in nested.fields], ['right', 'left'])
        self.assertEqual(nested.fields[0].states, (State,))
        self.assertEqual(nested.fields[0].reducer, nested.fields[1].reducer)
        self.assertEqual(nested.fields[0].reducer.transitions[0].actions, (Add,))
        self.assertEqual(info.fields[1].reducer.kind, 'function')
        self.assertEqual(info.fields[1].reducer.transitions[0].actions, (Add,))
        self.assertEqual(reducer(Root(), Add(2)).pair, Pair(State(2), State(2)))

    def test_transition_unions_and_snapshot_match_runtime(self):
        class Machine(BaseStateMachine):
            @transition
            def handle(self, state: State | Finished, action: Actions) -> State | Finished:
                if isinstance(action, Add):
                    return Finished(state.value + action.amount)
                return state

        machine = Machine()
        reducer = MachineReducer[State | Finished](machine)
        # Inspection must reuse the adapter's validated snapshot, not reread a
        # machine's registry or annotations after dispatch was configured.
        def changed_registry():
            raise AssertionError('registry read again')
        machine.transition_map = changed_registry
        info = describe_reducer(reducer)
        self.assertEqual(info.kind, 'machine')
        self.assertTrue(info.name.endswith('.Machine'))
        self.assertEqual(len(info.transitions), 1)
        handler = info.transitions[0]
        self.assertEqual(handler.sources, (State, Finished))
        self.assertEqual(handler.actions, (Add, Ignore))
        self.assertEqual(handler.destinations, (State, Finished))
        self.assertEqual(reducer(State(), Add(4)), Finished(4))
        previous = Finished(4)
        self.assertIs(reducer(previous, Ignore()), previous)

    def test_inheritance_override_and_unregistered_method(self):
        class Parent(Middleware[State, Actions]):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                ctx.next(action)

        class Inherited(Parent):
            pass

        class Override(Parent):
            @intercept(catch_exceptions=True)
            def handle(self, action: Ignore, ctx: Context) -> None:
                pass

        class Removed(Parent):
            def handle(self, action: Add, ctx: Context) -> None:
                pass

        inherited = describe_middleware(Inherited()).handlers[0]
        self.assertTrue(inherited.name.endswith('Parent.handle'))
        self.assertEqual(inherited.actions, (Add,))
        overridden = describe_middleware(Override()).handlers[0]
        self.assertTrue(overridden.name.endswith('Override.handle'))
        self.assertEqual(overridden.actions, (Ignore,))
        self.assertEqual(overridden.phase, 'manual')
        self.assertTrue(overridden.catch_exceptions)
        removed = describe_middleware(Removed())
        self.assertEqual(removed.kind, 'annotated')
        self.assertEqual(removed.handlers, ())
        subject = store(Removed())
        subject.dispatch(Add())
        self.assertEqual(subject.get_state(), State(1))

    def test_ambiguity_is_described_without_selecting_a_winner(self):
        class Overlap(Middleware[State, Actions]):
            @intercept_pre
            def broad(self, action: BaseAction, ctx: API) -> None:
                raise AssertionError('must not execute')

            @intercept_pre
            def specific(self, action: Add, ctx: API) -> None:
                raise AssertionError('must not execute')

        handlers = describe_middleware(Overlap()).handlers
        self.assertEqual([item.actions for item in handlers], [(BaseAction,), (Add,)])
        with self.assertRaises(AmbiguousHandlerError):
            store(Overlap()).dispatch(Add())

    def test_empty_declarations_are_distinct_from_opaque_callables(self):
        class Empty(BaseStateMachine):
            pass

        machine = describe_reducer(MachineReducer[State](Empty()))
        combined = describe_reducer(combine_reducers(Pair))
        middleware = describe_middleware(Middleware())
        self.assertEqual((machine.kind, machine.transitions), ('machine', ()))
        self.assertEqual((combined.kind, combined.fields), ('combined', ()))
        self.assertEqual((middleware.kind, middleware.handlers), ('annotated', ()))

    def test_custom_dispatch_overrides_are_opaque(self):
        class CustomMachine(MachineReducer[State]):
            def __call__(self, state, action):
                return state

        class CustomCombined(CombinedReducer[Pair]):
            def __call__(self, state, action):
                return state

        class CustomMiddleware(Middleware[State, Actions]):
            @intercept_pre
            def before(self, action: Add, ctx: API) -> None:
                raise AssertionError('not used by custom dispatch')

            def __call__(self, api, next_dispatch):
                return next_dispatch

        class Empty(BaseStateMachine):
            pass

        self.assertEqual(describe_reducer(CustomMachine(Empty())).kind, 'opaque')
        self.assertEqual(describe_reducer(CustomCombined(Pair)).kind, 'opaque')
        info = describe_middleware(CustomMiddleware())
        self.assertEqual(info.kind, 'opaque')
        self.assertEqual(info.handlers, ())

    def test_descriptions_are_immutable_and_do_not_expose_effect_objects(self):
        dependency = object()

        class Effect(Middleware[State, Actions]):
            def __init__(self):
                self.database = dependency

            @intercept_pre
            def before(self, action: Add, ctx: API) -> None:
                pass

        info = describe_store(store(Effect()))
        with self.assertRaises(FrozenInstanceError):
            info.reducer = None
        with self.assertRaises(FrozenInstanceError):
            info.middleware[0].handlers[0].phase = 'post'
        self.assertFalse(hasattr(info.middleware[0], 'database'))
        self.assertFalse(hasattr(info.middleware[0].handlers[0], 'original'))
        self.assertFalse(hasattr(info.reducer.transitions[0], 'invoke'))
        self.assertIsInstance(info.middleware, tuple)
        self.assertIsInstance(info.reducer.transitions, tuple)

    def test_reducer_inspection_does_not_predict_consumption(self):
        class Consume(Middleware[State, Actions]):
            @intercept
            def handle(self, action: Add, ctx: Context) -> None:
                pass

        subject = Store[State, Actions](initial_state=State(), reducer=adapter(), middleware=[Consume()])
        before = describe_store(subject)
        subject.dispatch(Add())
        self.assertEqual(subject.get_state(), State())
        self.assertEqual(describe_store(subject), before)
        self.assertEqual(before.reducer.transitions[0].actions, (Add,))
