from __future__ import annotations

from dataclasses import dataclass, field
import unittest

from typomata import BaseAction, BaseState, BaseStateMachine, transition
from typomata_redux import CombinedReducer, DefinitionError, MachineReducer, Store, combine_reducers


@dataclass(frozen=True)
class Add(BaseAction):
    amount: int = 1


class Ignore(BaseAction):
    pass


Actions = Add | Ignore


@dataclass(frozen=True)
class Count(BaseState):
    value: int = 0


@dataclass(frozen=True)
class History(BaseState):
    entries: tuple[int, ...] = ()


@dataclass(frozen=True)
class Root(BaseState):
    count: Count = Count()
    history: History = History()
    label: str = "unchanged"


@dataclass(frozen=True)
class Nested(BaseState):
    feature: Root = Root()
    other: Count = Count()


class Counter(BaseStateMachine):
    @transition
    def add(self, state: Count, action: Add) -> Count:
        return Count(state.value + action.amount)


class Recorder(BaseStateMachine):
    @transition
    def add(self, state: History, action: Add) -> History:
        return History((*state.entries, action.amount))


def counter():
    return MachineReducer[Count](Counter())


def recorder():
    return MachineReducer[History](Recorder())


class CombineTests(unittest.TestCase):
    def test_routes_action_to_every_configured_field(self):
        reducer = combine_reducers(Root, count=counter(), history=recorder())
        old = Root(label="keep me")
        action = Add(4)
        result = reducer(old, action)
        self.assertEqual(result, Root(Count(4), History((4,)), "keep me"))
        self.assertEqual(old, Root(label="keep me"))

    def test_noop_preserves_root_identity(self):
        reducer = combine_reducers(Root, count=counter(), history=recorder())
        old = Root()
        self.assertIs(reducer(old, Ignore()), old)

    def test_unconfigured_and_unchanged_fields_are_shared(self):
        reducer = combine_reducers(Root, count=counter())
        old = Root()
        new = reducer(old, Add())
        self.assertIsNot(new, old)
        self.assertIs(new.history, old.history)
        self.assertIs(new.label, old.label)
        self.assertEqual(new.count, Count(1))

    def test_equal_but_new_child_is_a_change(self):
        reducer = combine_reducers(Root, count=lambda state, action: Count(state.value))
        old = Root()
        new = reducer(old, Ignore())
        self.assertEqual(new, old)
        self.assertIsNot(new, old)
        self.assertIsNot(new.count, old.count)

    def test_empty_composition_is_identity(self):
        reducer = CombinedReducer[Root](Root)
        old = Root()
        self.assertIs(reducer(old, Add()), old)

    def test_nested_composition_preserves_unchanged_branches(self):
        feature = combine_reducers(Root, count=counter(), history=recorder())
        reducer = combine_reducers(Nested, feature=feature)
        old = Nested()
        new = reducer(old, Add(3))
        self.assertEqual(new.feature, Root(Count(3), History((3,))))
        self.assertIs(new.other, old.other)
        self.assertIs(reducer(new, Ignore()), new)

    def test_plain_reducers_receive_original_values_in_keyword_order(self):
        old = Root()
        events = []
        action = Add()

        def count(state, event):
            events.append((state, event))
            self.assertIs(state, old.count)
            return Count(5)

        def history(state, event):
            events.append((state, event))
            self.assertIs(state, old.history)
            return History((9,))

        combine_reducers(Root, count=count, history=history)(old, action)
        self.assertEqual(events, [(old.count, action), (old.history, action)])

    def test_failure_in_later_slice_does_not_commit_earlier_slice(self):
        failure = ValueError("history failed")

        def fails(state, action):
            raise failure

        old = Root()
        subject = Store[Root, Actions](
            initial_state=old,
            reducer=combine_reducers(Root, count=counter(), history=fails),
        )
        with self.assertRaises(ValueError) as caught:
            subject.dispatch(Add())
        self.assertIs(caught.exception, failure)
        self.assertIs(subject.get_state(), old)
        self.assertEqual(old.count, Count())

    def test_unknown_field_and_non_state_field_rejected(self):
        with self.assertRaisesRegex(DefinitionError, "unknown dataclass field"):
            combine_reducers(Root, coutn=counter())
        with self.assertRaises(DefinitionError):
            combine_reducers(Root, label=counter())

    def test_mismatched_adapter_rejected_at_construction(self):
        with self.assertRaisesRegex(DefinitionError, "Root.count"):
            combine_reducers(Root, count=recorder())
        with self.assertRaisesRegex(DefinitionError, "Nested.feature"):
            combine_reducers(Nested, feature=combine_reducers(Count))

    def test_transition_destination_checked_against_field_without_schema(self):
        class WrongDestination(BaseStateMachine):
            @transition
            def add(self, state: Count, action: Add) -> History:
                return History()

        # MachineReducer's generic is a caller declaration, not proof that the
        # supplied machine has that result type. Composition can catch this from
        # the real transition and dataclass field annotations.
        child = MachineReducer[Count](WrongDestination())
        with self.assertRaisesRegex(DefinitionError, "destination"):
            combine_reducers(Root, count=child)

    def test_broad_transition_source_is_compatible_with_specific_field(self):
        class Broad(BaseStateMachine):
            @transition
            def add(self, state: BaseState, action: Add) -> Count:
                return Count(3)

        reducer = combine_reducers(Root, count=MachineReducer[Count](Broad()))
        self.assertEqual(reducer(Root(), Add()).count, Count(3))

    def test_empty_machine_is_an_identity_slice(self):
        reducer = combine_reducers(Root, count=MachineReducer[Count](BaseStateMachine()))
        old = Root()
        self.assertIs(reducer(old, Add()), old)

    def test_union_field_allows_states_without_registered_transitions(self):
        @dataclass(frozen=True)
        class UnionRoot(BaseState):
            part: Count | History = Count()

        reducer = combine_reducers(UnionRoot, part=MachineReducer[Count | History](Counter()))
        self.assertEqual(reducer(UnionRoot(), Add()).part, Count(1))
        old = UnionRoot(History((4,)))
        self.assertIs(reducer(old, Add()), old)

    def test_plain_reducer_bad_result_rejected_before_commit(self):
        old = Root()
        subject = Store[Root, Actions](
            initial_state=old,
            reducer=combine_reducers(Root, count=lambda state, action: History()),
        )
        with self.assertRaisesRegex(TypeError, "Root.count result"):
            subject.dispatch(Add())
        self.assertIs(subject.get_state(), old)

    def test_bad_field_input_rejected_before_child_invocation(self):
        calls = []
        reducer = combine_reducers(Root, count=lambda state, action: calls.append(state))
        with self.assertRaisesRegex(TypeError, "Root.count input"):
            reducer(Root(count=History()), Add())
        self.assertEqual(calls, [])
        with self.assertRaisesRegex(TypeError, "combined reducer state"):
            reducer(Count(), Add())

    def test_invalid_root_and_async_children_rejected(self):
        with self.assertRaises(DefinitionError):
            combine_reducers(BaseState, count=counter())
        with self.assertRaises(DefinitionError):
            combine_reducers(Root(), count=counter())

        async def asynchronous(state, action):
            return state

        with self.assertRaises(DefinitionError):
            combine_reducers(Root, count=asynchronous)

    def test_non_init_field_cannot_be_reduced(self):
        @dataclass(frozen=True)
        class Derived(BaseState):
            count: Count = field(default=Count(), init=False)

        with self.assertRaisesRegex(DefinitionError, "init=False"):
            combine_reducers(Derived, count=counter())

    def test_union_fields_and_dataclass_inheritance(self):
        @dataclass(frozen=True)
        class UnionRoot(BaseState):
            part: Count | History = Count()

        def switch(state, action):
            return History() if isinstance(state, Count) else state

        reducer = combine_reducers(UnionRoot, part=switch)
        self.assertEqual(reducer(UnionRoot(), Add()).part, History())

        @dataclass(frozen=True)
        class ChildRoot(Root):
            extra: str = "child"

        root = ChildRoot()
        result = combine_reducers(ChildRoot, count=counter())(root, Add())
        self.assertEqual(result, ChildRoot(count=Count(1)))
        self.assertIsInstance(result, ChildRoot)


if __name__ == "__main__":
    unittest.main()
