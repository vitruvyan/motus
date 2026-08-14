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

import pytest

from vitruvyan_motus import GraphSpec, InMemoryTraceSink, Runtime, State
from vitruvyan_motus.errors import SinkFailed

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

    # `setattr` is the one indirect form a future edit plausibly reaches for,
    # and the plain assignment check cannot see it. The others a round found —
    # a `for` target, a `with ... as`, tuple unpacking — are not shapes anybody
    # writes here, and enumerating them all would be a check that looks
    # stronger than it is.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("setattr", "object")):
            literals = [a.value for a in node.args
                        if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            assert "_async_failure" not in literals, (
                f"line {node.lineno}: `_async_failure` set through setattr, "
                "which bypasses `_latch`")


def test_a_poison_reason_is_text_and_refuses_to_be_an_exception(tmp_path):
    """The second site the sweep found, and the reason it is clean.

    `CommitmentLog` outlives many calls, so an exception stored on it would
    pin a stack exactly as `_ObservationHub` did. It stores text — and the
    guard is exercised here rather than trusted, because an unexercised guard
    is indistinguishable from its own absence and is removed by the next person
    who finds it noisy.
    """
    from vitruvyan_motus.commitlog import CommitmentLog

    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1")
    log._poison("the disk went away")          # text is the contract
    assert log._poisoned == "the disk went away"

    other = CommitmentLog(tmp_path, tenant="acme", writer_id="w2")
    with pytest.raises(TypeError):
        other._poison(OSError(28, "No space left on device"))  # type: ignore[arg-type]
    assert other._poisoned is None


# -- the latch must not be able to fail -------------------------------------

class _RefusesToBeCopied(Exception):
    """An exception whose ``__new__`` demands arguments.

    Not contrived: `pydantic.ValidationError` and four of its siblings have
    this shape, and a sink may raise anything — `contract/guarantees.md` §6
    places no constraint on what `write` or `open_run` raises, and that is the
    whole protocol.
    """

    def __new__(cls, title, errors):
        return super().__new__(cls, title)

    def __init__(self, title, errors):
        super().__init__(title)
        self.errors = errors


def test_a_failure_that_cannot_be_copied_is_still_latched():
    """The invariant: **losing the failure is never an option.**

    An earlier version of `_detached` ran inside `except BaseException:` and
    was itself unprotected. When it raised, nothing was stored, the guard that
    refuses further writes never tripped, and the buffer was handed to the sink
    again — `[1, 2, 1, 2, 3, 1, 2, 3]` where `[1, 2]` was the truth. The caller
    was told `required trace sink failed: <the copy error>`, which is a false
    statement about what the sink did.
    """
    from vitruvyan_motus.observers import _detached

    try:
        raise _RefusesToBeCopied("the archive refused", [{"loc": "x"}])
    except _RefusesToBeCopied as caught:
        stored = _detached(caught)

    assert isinstance(stored, BaseException)
    assert stored.__traceback__ is None
    assert "the archive refused" in stored.__motus_origin__


def test_a_sink_that_raises_an_uncopyable_failure_is_not_retried(tmp_path):
    """The same defect through the runtime, which is where it did damage.

    `seq`s handed to the sink must be the ones the run produced, once. A
    truncated prefix is licensed by OPEN-08; a corrupted suffix is not.
    """
    seen: list[int] = []

    class RefusingSession:
        def write(self, batch):
            seen.extend(record["seq"] for record in batch)
            raise _RefusesToBeCopied("the archive refused", [])

        def finish(self, complete):
            pass

    class RefusingSink:
        def open_run(self, header):
            return RefusingSession()

    runtime = Runtime(SPEC, {"a": _passthrough}, sink=RefusingSink(),
                      durability_profile="buffered",
                      chunk_records=64, flush_interval_ms=0)
    with pytest.raises(Exception):
        runtime.run(State.empty("x"))

    assert len(seen) == len(set(seen)), (
        f"the same records were handed to the sink twice: {seen}")


def test_a_copy_that_returns_the_original_is_refused():
    """`__copy__` returning `self` would make the detachment mutate the
    exception still propagating to the caller — the one thing this must not
    do. It is refused and the next strategy is used."""
    from vitruvyan_motus.observers import _detached

    class ItsOwnCopy(Exception):
        def __copy__(self):
            return self

    original = None
    try:
        raise ItsOwnCopy("boom")
    except ItsOwnCopy as caught:
        original = caught
        stored = _detached(caught)
        assert caught.__traceback__ is not None, "the original kept its stack"

    assert stored is not original
    assert stored.__traceback__ is None


@pytest.mark.parametrize("build", [
    pytest.param(lambda inner: ExceptionGroup("fanned out", [inner]), id="group"),
    pytest.param(lambda inner: RuntimeError(inner), id="args"),
    pytest.param(lambda inner: SinkFailed(None, inner), id="attribute"),
])
def test_an_exception_reached_through_another_is_detached_too(build):
    """`__traceback__` is the obvious edge and it is not the only one.

    A group carries one traceback per member; an exception in `args` or on an
    attribute carries its own — and a shallow copy brings the attribute across.
    A round found all three still holding frames while the docstring claimed
    none did.
    """
    from vitruvyan_motus.observers import _detached

    try:
        raise ValueError("inner")
    except ValueError as inner:
        try:
            raise build(inner)
        except BaseException as outer:
            stored = _detached(outer)

    assert not _frames_reachable_from(stored), (
        "a frame is still reachable from the stored failure")


def _frames_reachable_from(exc, depth=6, seen=None):
    """Every frame or traceback reachable along the edges `_strip` walks."""
    import types

    seen = set() if seen is None else seen
    if depth <= 0 or id(exc) in seen:
        return []
    seen.add(id(exc))
    found = []
    for field in ("__traceback__", "__cause__", "__context__"):
        value = getattr(exc, field, None)
        if isinstance(value, types.TracebackType):
            found.append(field)
        elif isinstance(value, BaseException):
            found.extend(_frames_reachable_from(value, depth - 1, seen))
    reachable = list(getattr(exc, "args", ()))
    reachable.extend(getattr(exc, "exceptions", ()) or ())
    try:
        reachable.extend(vars(exc).values())
    except TypeError:
        pass
    for value in reachable:
        if isinstance(value, BaseException):
            found.extend(_frames_reachable_from(value, depth - 1, seen))
    return found


def test_re_raising_the_stored_failure_does_not_give_it_a_stack(tmp_path):
    """`raise self._async_failure` repopulated the stored object's traceback as
    it propagated, so a live `Runtime` rooted the last failed run's frames —
    the same chain #99 names, one run deep. The readers raise a fresh copy."""
    from vitruvyan_motus.observers import _ObservationHub

    hub = _ObservationHub(profile="synchronous", sink=InMemoryTraceSink(),
                          listeners=(), chunk_records=64, flush_interval_ms=0)
    try:
        raise ValueError("boom")
    except ValueError as caught:
        hub._latch(caught)

    for _ in range(3):
        with pytest.raises(ValueError):
            hub.persist({"seq": 1, "kind": "run_started"})
        assert hub._async_failure.__traceback__ is None
