"""Function-first composition preserves narrow bodies and typed state/results."""
from typing_extensions import assert_never, assert_type
from typomata_redux import FunctionReducer, Store, combine_reducers
from consumer import Add, Count, Ignore, Root


def counter(state: Count, action: Add | Ignore) -> Count:
    if isinstance(action, Add):
        return Count(state.value + action.amount)
    if isinstance(action, Ignore):
        return state
    assert_never(action)


root = combine_reducers(Root, count=counter)
app = Store[Root, Add | Ignore](initial_state=Root(), reducer=root)
assert_type(app.get_state(), Root)
assert_type(root(Root(), object()), Root)
adapted = FunctionReducer(counter)
assert_type(adapted(Count(), object()), Count)
assert_type(adapted(Count(), Add()), Count)
counter(Count(), object())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
adapted(Root(), Add())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


def incomplete(state: Count, action: Add | Ignore) -> Count:
    if isinstance(action, Add):
        return Count(state.value + action.amount)
    assert_never(action)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


def invalid_result(state: Count, action: Add) -> Root:
    return Root()


FunctionReducer(invalid_result)  # type: ignore[misc]  # pyright: ignore[reportArgumentType]
Store[Count, Add](initial_state=Count(), reducer=invalid_result)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


# Roots and slices both adapt narrow functions without losing the store vocabulary.
def only_add(state: Count, action: Add) -> Count:
    return Count(state.value + action.amount)


standalone = Store[Count, Add | Ignore](initial_state=Count(), reducer=only_add)
assert_type(standalone.get_state(), Count)
standalone.dispatch(Ignore())
standalone.dispatch(object())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
covered = Store[Count, Add | Ignore](initial_state=Count(), reducer=counter,
                                    required_actions=Add | Ignore)
assert_type(covered.get_state(), Count)
covered.dispatch(object())  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


class Service:
    def __init__(self, offset: int) -> None:
        self.offset = offset

    def reduce(self, state: Count, event: Add) -> Count:
        return Count(state.value + event.amount + self.offset)


assert_type(FunctionReducer(Service(2).reduce)(Count(), Add()), Count)
combine_reducers(Root, count=Service(2).reduce)
