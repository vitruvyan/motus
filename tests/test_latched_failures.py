"""A failure latched on a long-lived object must not pin the stack that raised it.

The defect these pin (#99): an exception keeps its ``__traceback__``, a
traceback keeps the frame that raised, and a frame keeps ``f_back``. So storing
one failure on an object that outlives the call keeps the whole call stack
alive — and in this runtime that stack held the stream driver whose collection
was the signal to release the run. A `Runtime` whose sink failed at `bind`
claimed a run that had never executed a node, for good, and fifty of them
survived ten full collections with `gc.garbage` empty.

It is a class rather than a site, and the class is: **storing an exception on a
long-lived object pins every frame beneath it.**
"""

from __future__ import annotations

import gc
import weakref

from vitruvyan_motus import GraphSpec, InMemoryTraceSink, Runtime, State

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "latched", "version": "1.0.0",
    "entry": "a", "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})


def _passthrough(state):
    return state




def test_a_driver_whose_sink_failed_at_bind_does_not_wedge_the_runtime():
    """#99, and the symptom is the one that matters: the run is never released.

    `Runtime._release_if_never_started` exists to make exactly this not happen,
    and it never ran — the `weakref.finalize` never fired, because the stored
    failure pinned the stack that raised it and that stack still held the
    driver. The claim outlived every reference to it, so the Runtime kept
    claiming a run that had not executed one node.
    """
    class FailsOnce:
        def __init__(self) -> None:
            self.calls = 0
            self.inner = InMemoryTraceSink()

        def open_run(self, header):
            self.calls += 1
            if self.calls == 1:
                raise OSError(28, "No space left on device")
            return self.inner.open_run(header)

    sink = FailsOnce()
    runtime = Runtime(SPEC, {"a": _passthrough}, sink=sink)
    driver = runtime.stream(State.empty("x"))
    del driver
    gc.collect()

    result = runtime.run(State.empty("y"))
    assert result.status == "completed"
    assert result.evidence == "persisted"


def test_the_abandoned_driver_and_its_runtime_are_collected():
    """The leak beside the wedge, measured rather than argued.

    Fifty abandoned drivers whose sink failed at bind used to survive ten full
    collections with their Runtimes, and `gc.garbage` was empty — so this was
    never a cycle the collector could not break. It was a live reference chain:
    exception, traceback, frame, `f_back`, and `stream()`'s local `driver`.
    """
    class DeadSink:
        def open_run(self, header):
            raise OSError(28, "No space left on device")

    drivers, runtimes = [], []
    for _ in range(20):
        runtime = Runtime(SPEC, {"a": _passthrough}, sink=DeadSink())
        driver = runtime.stream(State.empty("x"))
        drivers.append(weakref.ref(driver))
        runtimes.append(weakref.ref(runtime))
        del runtime, driver
    for _ in range(3):
        gc.collect()

    assert sum(1 for ref in drivers if ref() is not None) == 0
    assert sum(1 for ref in runtimes if ref() is not None) == 0


def test_a_latched_failure_holds_no_frame():
    """The mechanism, pinned directly rather than through its symptom.

    A test that only measured collection would pass again the day somebody
    reintroduces the pin behind a second reference that happens to die.
    """
    from vitruvyan_motus.observers import _detached

    marker = object()

    def raises_with_a_local():
        held_by_this_frame = marker              # noqa: F841 - the point
        raise ValueError("boom")

    try:
        raises_with_a_local()
    except ValueError as caught:
        assert caught.__traceback__ is not None, "the original keeps its traceback"
        stored = _detached(caught)

    assert stored.__traceback__ is None
    assert stored.__cause__ is None
    assert stored.__context__ is None
    assert type(stored) is ValueError and stored.args == ("boom",)
    assert "raises_with_a_local" in stored.__motus_origin__, (
        "the formatted original is kept; only the frames are dropped")


def test_the_original_exception_is_never_mutated():
    """The immediate caller's diagnosis is not traded for the stored copy's.

    `persist` and `_flush_locked` re-raise after latching, so the exception the
    caller catches must still carry the stack it was raised from.
    """
    from vitruvyan_motus.observers import _detached

    try:
        raise ValueError("boom")
    except ValueError as caught:
        _detached(caught)
        assert caught.__traceback__ is not None


def test_a_chained_failure_drops_its_chain():
    """A chained exception pins its own stack, so the chain is cut too."""
    from vitruvyan_motus.observers import _detached

    try:
        try:
            raise KeyError("inner")
        except KeyError as inner:
            raise ValueError("outer") from inner
    except ValueError as caught:
        assert caught.__cause__ is not None
        stored = _detached(caught)

    assert stored.__cause__ is None and stored.__context__ is None


def test_an_exception_whose_init_refuses_its_args_still_detaches():
    """`DeclarationViolation` takes a list and its args are a string, so a copy
    raises. The type and the args survive anyway, because those are what a
    caller reads off `SinkFailed.cause`."""
    from vitruvyan_motus.errors import DeclarationViolation
    from vitruvyan_motus.observers import _detached

    try:
        raise DeclarationViolation([{"kind": "read", "key": "k"}])
    except DeclarationViolation as caught:
        original_args = caught.args
        stored = _detached(caught)

    assert type(stored) is DeclarationViolation
    assert stored.args == original_args
    assert stored.__traceback__ is None


def test_the_failure_is_latched_in_one_place_and_a_parser_says_so():
    """The rule is in the tool, not in three call sites that must remember it.

    There were three assignments to `_async_failure` and every one had to
    remember to detach. Now there is one, and this test reads the module rather
    than trusting a comment — parsed, because the question is *where is this
    attribute assigned* and a pattern over the text answers a different one.
    """
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "src" / "vitruvyan_motus" / "observers.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assigners: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            for target in targets:
                if (isinstance(target, ast.Attribute)
                        and target.attr == "_async_failure"):
                    assigners.add(function.name)

    assert assigners == {"__init__", "_latch"}, (
        f"`_async_failure` is assigned in {sorted(assigners)}; every store must "
        "go through `_latch`, which is the only thing that detaches the stack")
