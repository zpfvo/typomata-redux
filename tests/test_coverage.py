"""Required middleware vocabularies are checked before a store is constructed."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from types import new_class
from typing import Annotated, Any, Protocol, TypeVar, Union
import unittest

from typomata_redux import (
    DefinitionError, Middleware, MiddlewareContext, Store, StoreAPI,
    intercept, intercept_post, intercept_pre,
)
from typomata_redux._inspection import describe_middleware


@dataclass(frozen=True)
class Add:
    amount: int = 1


class Reset:
    pass


class Decrement:
    pass


class SpecialAdd(Add):
    pass


Actions = Add | Reset | Decrement
API = StoreAPI[int, Actions]
Context = MiddlewareContext[int, Actions]
PHASES = ((intercept, 'manual_actions'), (intercept_pre, 'pre_actions'),
          (intercept_post, 'post_actions'))


def declare(decorator, annotation, **contracts):
    """Exercise identical coverage rules through each public decorator."""
    def handle(self, action, ctx):
        raise AssertionError('definition must not invoke the handler')

    handle.__annotations__ = {
        'action': annotation, 'ctx': Context if decorator is intercept else API,
        'return': None,
    }
    return new_class('Effects', (Middleware[int, Actions],), contracts,
                     lambda namespace: namespace.update(handle=decorator(handle)))


class CoverageTests(unittest.TestCase):
    def test_every_used_phase_requires_a_declaration(self):
        for decorator, keyword in PHASES:
            for contracts in ({}, {keyword: None}):
                with self.subTest(keyword=keyword, contracts=contracts):
                    with self.assertRaisesRegex(DefinitionError, keyword + ': declare'):
                        declare(decorator, Add, **contracts)

    def test_union_growth_requires_a_handler_in_each_phase(self):
        for decorator, keyword in PHASES:
            with self.subTest(keyword=keyword):
                declare(decorator, Add, **{keyword: Add})
                with self.assertRaisesRegex(DefinitionError, 'missing .* handler for Reset'):
                    declare(decorator, Add, **{keyword: Add | Reset})

    def test_declaration_requires_handlers_even_when_all_are_missing(self):
        for _, keyword in PHASES:
            with self.subTest(keyword=keyword):
                with self.assertRaisesRegex(DefinitionError, 'missing .* handler for Add'):
                    type('Empty', (Middleware,), {}, **{keyword: Add})

    def test_handlers_cannot_exceed_the_declared_vocabulary(self):
        for decorator, keyword in PHASES:
            for annotation in (Reset, Add | Reset, object):
                with self.subTest(keyword=keyword, annotation=annotation):
                    with self.assertRaisesRegex(DefinitionError, 'outside declared .*' + keyword):
                        declare(decorator, annotation, **{keyword: Add})

    def test_another_phase_cannot_supply_missing_coverage(self):
        with self.assertRaisesRegex(DefinitionError, 'missing pre handler for Add'):
            declare(intercept_post, Add, pre_actions=Add, post_actions=Add)

    def test_invalid_phase_declarations_fail_even_without_handlers(self):
        class Structural(Protocol):
            pass

        for annotation in (
            Any, list[Add], dict[str, Add], tuple[Add, ...],
            Annotated[list[Add], 'items'], Add | list[Add],
            Annotated[Add | list[Add], 'actions'],
            TypeVar('T'), Structural, 'Add', (Add,), (), False,
        ):
            with self.subTest(annotation=annotation):
                with self.assertRaisesRegex(DefinitionError, 'pre_actions: expected concrete classes'):
                    type('Invalid', (Middleware,), {}, pre_actions=annotation)

    def test_union_and_annotated_normalization_and_immutable_inspection(self):
        expected = Annotated[Union[Add, Reset], 'required']
        subject = declare(intercept_pre, Annotated[Add | Reset, 'handled'], pre_actions=expected)
        info = describe_middleware(subject())
        pre = next(item for item in info.coverage if item.phase == 'pre')
        self.assertEqual(pre.actions, (Add, Reset))
        self.assertEqual(info.handlers[0].actions, (Add, Reset))
        with self.assertRaises(FrozenInstanceError):
            pre.actions = ()
        self.assertEqual(next(item for item in info.coverage if item.phase == 'post').actions, ())

    def test_split_handlers_cover_independent_phases_and_preserve_constructors(self):
        events = []

        class Effects(Middleware[int, Actions], pre_actions=Add | Reset,
                      post_actions=Add, manual_actions=Decrement):
            def __init__(self, output):
                self.output = output

            @intercept_pre
            def add(self, action: Add, ctx: API) -> None:
                self.output.append(('add', ctx.get_state()))

            @intercept_pre
            def reset(self, action: Reset, ctx: API) -> None:
                self.output.append(('reset', ctx.get_state()))

            @intercept_post
            def after(self, action: Add, ctx: API) -> None:
                self.output.append(('post', ctx.get_state()))

            @intercept
            def decrement(self, action: Decrement, ctx: Context) -> None:
                self.output.append(('manual', ctx.get_state()))
                ctx.next(action)

        self.assertEqual(events, [])
        def increment(state: int, action: object) -> int:
            return state + 1

        subject = Store(initial_state=0, reducer=increment,
                        middleware=[Effects(events)])
        subject.dispatch(Add())
        subject.dispatch(Reset())
        subject.dispatch(Decrement())
        subject.dispatch(object())  # Unrelated actions still forward.
        self.assertEqual(events, [('add', 0), ('post', 1), ('reset', 1), ('manual', 2)])
        self.assertEqual(subject.get_state(), 4)

    def test_superclass_handlers_cover_subclasses(self):
        seen = []

        class Effects(Middleware[int, Actions], pre_actions=Add | SpecialAdd):
            @intercept_pre
            def before(self, action: Add, ctx: API) -> None:
                seen.append(action)

        action = SpecialAdd()
        def unchanged(state: int, action: object) -> int:
            return state

        Store(initial_state=0, reducer=unchanged,
              middleware=[Effects()]).dispatch(action)
        self.assertEqual(seen, [action])
        with self.assertRaisesRegex(DefinitionError, 'missing pre handler for Add'):
            declare(intercept_pre, SpecialAdd, pre_actions=Add)

    def test_known_subclass_overlap_is_rejected_at_definition(self):
        for expected in (Add, Add | SpecialAdd):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(DefinitionError, 'conflicting interceptors for SpecialAdd'):
                    class Overlap(Middleware[int, Actions], pre_actions=expected):
                        @intercept_pre
                        def broad(self, action: Add, ctx: API) -> None:
                            raise AssertionError('not invoked')

                        @intercept_pre
                        def narrow(self, action: SpecialAdd, ctx: API) -> None:
                            raise AssertionError('not invoked')

    def test_known_manual_automatic_overlap_is_rejected_at_definition(self):
        with self.assertRaisesRegex(DefinitionError, 'conflicting interceptors for SpecialAdd'):
            class Overlap(Middleware[int, Actions], manual_actions=Add, post_actions=SpecialAdd):
                @intercept
                def broad(self, action: Add, ctx: Context) -> None:
                    pass

                @intercept_post
                def narrow(self, action: SpecialAdd, ctx: API) -> None:
                    pass

    def test_inherited_contract_is_revalidated_after_method_removal(self):
        parent = declare(intercept_pre, Add, pre_actions=Add)

        class Inherited(parent):
            pass

        self.assertEqual(describe_middleware(Inherited()).coverage,
                         describe_middleware(parent()).coverage)
        with self.assertRaisesRegex(DefinitionError, 'missing pre handler for Add'):
            class Removed(parent):
                def handle(self, action, ctx):
                    pass

        class Cleared(parent, pre_actions=None):
            def handle(self, action, ctx):
                pass

        self.assertEqual(describe_middleware(Cleared()).handlers, ())
        with self.assertRaisesRegex(DefinitionError, 'pre_actions: declare'):
            class StillRegistered(parent, pre_actions=None):
                pass

    def test_explicit_subclass_contract_replaces_parent_contract(self):
        parent = declare(intercept_pre, Add, pre_actions=Add)
        with self.assertRaisesRegex(DefinitionError, 'outside declared .*pre_actions'):
            class Wrong(parent):
                @intercept_pre
                def handle(self, action: Reset, ctx: API) -> None:
                    pass

        class Replaced(parent, pre_actions=Reset):
            @intercept_pre
            def handle(self, action: Reset, ctx: API) -> None:
                pass

        self.assertEqual(describe_middleware(Replaced()).handlers[0].actions, (Reset,))
        self.assertEqual(describe_middleware(parent()).handlers[0].actions, (Add,))

    def test_subclass_additions_require_extending_the_contract(self):
        parent = declare(intercept_pre, Add, pre_actions=Add)
        with self.assertRaisesRegex(DefinitionError, 'outside declared .*pre_actions'):
            class Wrong(parent):
                @intercept_pre
                def reset(self, action: Reset, ctx: API) -> None:
                    pass

        class Extended(parent, pre_actions=Add | Reset):
            @intercept_pre
            def reset(self, action: Reset, ctx: API) -> None:
                pass

        self.assertEqual(len(describe_middleware(Extended()).handlers), 2)

    def test_multiple_inheritance_merges_contracts_and_deduplicates_diamonds(self):
        class First(Middleware[int, Actions], pre_actions=Add):
            @intercept_pre
            def add(self, action: Add, ctx: API) -> None:
                pass

        class Second(Middleware[int, Actions], pre_actions=Reset):
            @intercept_pre
            def reset(self, action: Reset, ctx: API) -> None:
                pass

        class Both(First, Second):
            pass

        class Another(First):
            pass

        class Diamond(Both, Another):
            pass

        info = describe_middleware(Diamond())
        self.assertEqual(next(item for item in info.coverage if item.phase == 'pre').actions, (Add, Reset))
        self.assertEqual(len(info.handlers), 2)

        class Shadow(Middleware[int, Actions], pre_actions=Reset):
            @intercept_pre
            def add(self, action: Reset, ctx: API) -> None:
                pass

        with self.assertRaisesRegex(DefinitionError, 'missing pre handler for Reset'):
            class Missing(First, Shadow):
                pass

    def test_failed_class_does_not_register_or_change_reused_decorated_methods(self):
        @intercept_pre
        def before(self, action: Add, ctx: API) -> None:
            pass

        with self.assertRaisesRegex(DefinitionError, 'missing pre handler for Reset'):
            type('Invalid', (Middleware,), {'before': before}, pre_actions=Add | Reset)
        valid = new_class('Valid', (Middleware[int, Actions],), {'pre_actions': Add},
                          lambda namespace: namespace.update(before=before))
        valid().before(Add(), API(lambda: 0, lambda action: None))
        with self.assertRaisesRegex(DefinitionError, 'missing pre handler for Reset'):
            type('InvalidChild', (valid,), {}, pre_actions=Add | Reset)
        valid().before(Add(), API(lambda: 0, lambda action: None))

    def test_coverage_is_snapshotted_and_does_not_inspect_store_generics(self):
        vocabulary = Add
        subject = declare(intercept_pre, Add, pre_actions=vocabulary)
        vocabulary = Add | Reset
        pre = next(item for item in describe_middleware(subject()).coverage if item.phase == 'pre')
        self.assertEqual(pre.actions, (Add,))
        # Phase vocabulary must now fit the middleware's declared action type.
        with self.assertRaisesRegex(DefinitionError, 'outside Middleware action vocabulary'):
            class ForeignVocabulary(Middleware[int, Reset], pre_actions=Add):
                @intercept_pre
                def before(self, action: Add, ctx: StoreAPI[int, Reset]) -> None:
                    pass
