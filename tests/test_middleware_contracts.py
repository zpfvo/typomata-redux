"""Declarations must agree before a handler can observe the wrong store types."""
from __future__ import annotations

from types import new_class
from typing import Annotated, Any, Generic, TypeVar
import unittest

from typomata_redux import (
    DefinitionError, Middleware, MiddlewareContext, StoreAPI,
    intercept, intercept_post, intercept_pre,
)


class Add:
    pass


class Reset:
    pass


class Foreign:
    pass


Actions = Add | Reset
S = TypeVar('S')
A = TypeVar('A')
T = TypeVar('T')
U = TypeVar('U')
PHASES = ((intercept, 'manual_actions', MiddlewareContext),
          (intercept_pre, 'pre_actions', StoreAPI),
          (intercept_post, 'post_actions', StoreAPI))


def declare(decorator, keyword, context, *, action=Add, vocabulary=Add,
            base=Middleware[int, Actions]):
    def handle(self, action, ctx):
        ctx.get_state()

    handle.__annotations__ = {'action': action, 'ctx': context, 'return': None}
    return new_class('Effects', (base,), {keyword: vocabulary},
                     lambda namespace: namespace.update(handle=decorator(handle)))


def bind(effect):
    seen = []
    effect(StoreAPI(lambda: 3, lambda action: None), seen.append)(Add())
    return seen


class ContractTests(unittest.TestCase):
    def test_each_phase_rejects_wrong_context_state_and_actions(self):
        for decorator, keyword, context in PHASES:
            for hint, message in ((context[str, Actions], 'context state'),
                                  (context[int, Foreign], 'context actions'),
                                  (context[int, Add], 'context actions')):
                with self.subTest(keyword=keyword, hint=hint):
                    with self.assertRaisesRegex(DefinitionError, message):
                        declare(decorator, keyword, hint)

    def test_each_phase_rejects_actions_outside_owner(self):
        for decorator, keyword, context in PHASES:
            with self.subTest(keyword=keyword):
                with self.assertRaisesRegex(DefinitionError, 'outside Middleware action vocabulary'):
                    declare(decorator, keyword, context[int, Actions],
                            action=Foreign, vocabulary=Foreign)

    def test_equivalent_union_order_and_annotated_arguments(self):
        for decorator, keyword, context in PHASES:
            effect = declare(decorator, keyword,
                             context[Annotated[int, 'state'], Annotated[Reset | Add, 'actions']])
            bind(effect())

    def test_narrow_subclass_and_broad_object_contracts(self):
        class Special(Add):
            pass

        declare(intercept_pre, 'pre_actions', StoreAPI[int, Actions],
                action=Special, vocabulary=Special)
        declare(intercept_pre, 'pre_actions', StoreAPI[int, object],
                action=object, vocabulary=object, base=Middleware[int, object])

    def test_any_and_unparameterized_owners_do_not_bypass_checks(self):
        for context in (StoreAPI[Any, Actions], StoreAPI[int, Any]):
            with self.assertRaisesRegex(DefinitionError, 'expected concrete classes'):
                declare(intercept_pre, 'pre_actions', context)
        with self.assertRaisesRegex(DefinitionError, 'concrete Middleware'):
            declare(intercept_pre, 'pre_actions', StoreAPI[int, Actions], base=Middleware)
        with self.assertRaisesRegex(DefinitionError, 'expected concrete classes'):
            declare(intercept_pre, 'pre_actions', StoreAPI[int, Actions], base=Middleware[Any, Actions])

    def test_generic_context_is_resolved_on_concrete_subclass(self):
        class GenericEffect(Middleware[S, A], pre_actions=Add):
            @intercept_pre
            def before(self, action: Add, ctx: StoreAPI[S, A]) -> None:
                self.observed = ctx.get_state()

        class Concrete(GenericEffect[int, Actions]):
            pass

        class Inherited(Concrete):
            pass

        for cls in (Concrete, Inherited):
            instance = cls()
            self.assertEqual(len(bind(instance)), 1)
            self.assertEqual(instance.observed, 3)
        with self.assertRaisesRegex(DefinitionError, 'outside Middleware action vocabulary'):
            class Invalid(GenericEffect[int, Reset]):
                pass

    def test_generic_instance_specialization_is_checked_at_binding(self):
        class GenericEffect(Middleware[S, Actions], pre_actions=Add):
            @intercept_pre
            def before(self, action: Add, ctx: StoreAPI[int, Actions]) -> None:
                pass

        bind(GenericEffect[int]())
        with self.assertRaisesRegex(DefinitionError, 'context state'):
            bind(GenericEffect[str]())
        with self.assertRaisesRegex(DefinitionError, 'concrete Middleware'):
            bind(GenericEffect())

    def test_partial_generic_substitution_and_reordered_parameters(self):
        class GenericEffect(Middleware[S, A], pre_actions=Add):
            @intercept_pre
            def before(self, action: Add, ctx: StoreAPI[S, A]) -> None:
                pass

        class Reordered(GenericEffect[U, T], Generic[T, U]):
            pass

        class Partial(Reordered[Actions, T]):
            pass

        class Concrete(Partial[int]):
            pass

        bind(Concrete())

    def test_concrete_context_in_generic_base_is_revalidated(self):
        class GenericEffect(Middleware[S, Actions], pre_actions=Add):
            @intercept_pre
            def before(self, action: Add, ctx: StoreAPI[int, Actions]) -> None:
                pass

        with self.assertRaisesRegex(DefinitionError, 'context state'):
            class Invalid(GenericEffect[str]):
                pass
        class Valid(GenericEffect[int]):
            pass
        bind(Valid())

    def test_conflicting_generic_inheritance_is_rejected_even_without_handlers(self):
        class First(Middleware[int, Actions]):
            pass

        class WrongState(Middleware[str, Actions]):
            pass

        class WrongActions(Middleware[int, Add]):
            pass

        for other in (WrongState, WrongActions):
            with self.assertRaisesRegex(DefinitionError, 'inherited'):
                type('Conflict', (First, other), {})

    def test_invalid_override_does_not_change_parent_registration(self):
        class Parent(Middleware[int, Actions], pre_actions=Add):
            @intercept_pre
            def before(self, action: Add, ctx: StoreAPI[int, Actions]) -> None:
                pass

        with self.assertRaisesRegex(DefinitionError, 'context state'):
            class Invalid(Parent):
                @intercept_pre
                def before(self, action: Add, ctx: StoreAPI[str, Actions]) -> None:
                    pass
        bind(Parent())

    def test_valid_generic_diamond(self):
        class GenericEffect(Middleware[S, Actions], pre_actions=Add):
            @intercept_pre
            def before(self, action: Add, ctx: StoreAPI[S, Actions]) -> None:
                pass

        class Left(GenericEffect[int]):
            pass

        class Right(GenericEffect[int]):
            pass

        class Diamond(Left, Right):
            pass

        self.assertEqual(len(bind(Diamond())), 1)

    def test_unbound_context_typevar_cannot_hide_a_mismatch(self):
        with self.assertRaisesRegex(DefinitionError, 'unresolved TypeVars'):
            declare(intercept_pre, 'pre_actions', StoreAPI[T, Actions])
