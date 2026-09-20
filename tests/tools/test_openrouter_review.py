"""Tests for the OpenRouter review script.

The script is loaded from its path (it is a CI artifact, not an installed
module) and the network is never touched: ``call_openrouter`` takes its HTTP
opener as a seam, and these tests hand it a fake client.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "openrouter_review.py"


@pytest.fixture
def review_mod():
    """A fresh copy of the script, so no test can leak state into another."""
    spec = importlib.util.spec_from_file_location(
        "openrouter_review_under_test", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class FakeHTTPClient:
    """Records the requests and replies with a canned body or error."""

    def __init__(self, *, body: bytes | None = None, error: Exception | None = None):
        self.body = body
        self.error = error
        self.requests: list = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return FakeResponse(self.body or b"{}")


# --- build_payload: model, messages, doctrine -------------------------------


def test_payload_carries_model_messages_and_doctrine(review_mod):
    payload = review_mod.build_payload("diff body", "some/model", "DOCTRINE TEXT")

    assert payload["model"] == "some/model"
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert "DOCTRINE TEXT" in payload["messages"][0]["content"]
    assert payload["messages"][1]["content"] == "diff body"


def test_a_long_diff_is_truncated_with_a_marker(review_mod):
    diff = "x" * (review_mod.MAX_DIFF_CHARS + 500)
    payload = review_mod.build_payload(diff, "m", "d")
    user = payload["messages"][1]["content"]

    assert user.endswith("[diff truncated for length]")
    assert user.startswith("x" * 100)
    assert len(user) < len(diff)


def test_a_short_diff_is_left_alone(review_mod):
    payload = review_mod.build_payload("short diff", "m", "d")

    assert payload["messages"][1]["content"] == "short diff"
    assert "truncated" not in payload["messages"][1]["content"]


# --- doctrine: the class of files, not two names ----------------------------


def test_doctrine_is_every_instruction_file_under_a_path_heading(review_mod, tmp_path):
    files = {
        "AGENTS.md": "root rules",
        "CLAUDE.md": "claude delta",
        ".github/copilot-instructions.md": "copilot rules",
        ".pi/AGENTS.md": "pi rules",
        "src/foo/AGENTS.md": "nested component rules",
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    doctrine = review_mod.load_guidelines(str(tmp_path), "+++ b/src/foo/bar.py\n")

    positions = []
    for rel, text in files.items():
        assert f"## {rel}" in doctrine
        assert text in doctrine
        positions.append(doctrine.index(f"## {rel}"))
    assert positions == sorted(positions), "doctrine order must be stable"


def test_a_missing_instruction_file_is_simply_absent(review_mod, tmp_path):
    (tmp_path / "AGENTS.md").write_text("only the root", encoding="utf-8")

    doctrine = review_mod.load_guidelines(str(tmp_path), "")

    assert "only the root" in doctrine
    assert "CLAUDE" not in doctrine


def test_no_doctrine_at_all_says_so(review_mod, tmp_path):
    doctrine = review_mod.load_guidelines(str(tmp_path), "")

    assert "no repository guidelines found" in doctrine


def test_only_touched_directories_contribute_nested_doctrine(review_mod, tmp_path):
    for name in ("touched", "untouched"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "AGENTS.md").write_text(
            f"{name} rules", encoding="utf-8"
        )

    doctrine = review_mod.load_guidelines(str(tmp_path), "+++ b/touched/mod.py\n")

    assert "touched rules" in doctrine
    assert "untouched rules" not in doctrine


def test_a_guideline_present_only_in_the_pull_request_does_not_enter(
    review_mod, tmp_path
):
    # The workflow runs on pull_request_target and checks out the BASE revision.
    # A path the diff names whose AGENTS.md exists only on the PR head is not
    # read: it is outside --repo-root, and doctrine comes from the base only.
    repo_root = tmp_path / "base"
    repo_root.mkdir()
    (tmp_path / "pr-only").mkdir()
    (tmp_path / "pr-only" / "AGENTS.md").write_text(
        "PR-ONLY-DOCTRINE", encoding="utf-8"
    )
    diff = "+++ b/pr-only/mod.py\n"

    assert review_mod.nested_guideline_paths(str(repo_root), diff) == []
    assert "PR-ONLY-DOCTRINE" not in review_mod.load_guidelines(str(repo_root), diff)


def test_a_diff_path_cannot_escape_the_repo_root(review_mod, tmp_path):
    repo_root = tmp_path / "base"
    repo_root.mkdir()
    (tmp_path / "AGENTS.md").write_text("OUTSIDE-DOCTRINE", encoding="utf-8")
    diff = "+++ b/../mod.py\n"

    assert review_mod.nested_guideline_paths(str(repo_root), diff) == []
    assert "OUTSIDE-DOCTRINE" not in review_mod.load_guidelines(str(repo_root), diff)


def test_a_symlink_pointing_outside_the_root_is_not_followed(review_mod, tmp_path):
    # `sub` is spelled inside the root but resolves outside it. `abspath`
    # would have called that safely inside; the doctrine is read where the
    # bytes actually live, so the link is refused.
    repo_root = tmp_path / "base"
    repo_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "AGENTS.md").write_text("OUTSIDE-DOCTRINE", encoding="utf-8")
    os.symlink(outside, repo_root / "sub")
    diff = "+++ b/sub/mod.py\n"

    assert review_mod.nested_guideline_paths(str(repo_root), diff) == []
    assert "OUTSIDE-DOCTRINE" not in review_mod.load_guidelines(str(repo_root), diff)


def test_a_symlink_pointing_inside_the_root_is_followed(review_mod, tmp_path):
    # The counterpart: resolution must not reject a link merely because it is
    # a link. A target that really is inside the root is doctrine.
    repo_root = tmp_path / "base"
    (repo_root / "real").mkdir(parents=True)
    (repo_root / "real" / "AGENTS.md").write_text("INSIDE-DOCTRINE", encoding="utf-8")
    os.symlink(repo_root / "real", repo_root / "alias")
    diff = "+++ b/alias/mod.py\n"

    assert review_mod._stays_inside(str(repo_root), "alias/AGENTS.md")
    assert "INSIDE-DOCTRINE" in review_mod.load_guidelines(str(repo_root), diff)


def test_nested_doctrine_is_ordered_by_proximity_not_alphabet(review_mod, tmp_path):
    # `aaa` sorts before `zzz`, but `zzz` is where the change actually is:
    # three touched files against one. Proximity wins over the alphabet.
    for name in ("aaa", "zzz"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "AGENTS.md").write_text(f"{name} rules", encoding="utf-8")
    diff = "+++ b/aaa/one.py\n" + "".join(
        f"+++ b/zzz/mod{index}.py\n" for index in range(3)
    )

    names = review_mod.nested_guideline_paths(str(tmp_path), diff)

    assert names[0] == "zzz/AGENTS.md"


def test_nested_doctrine_breaks_proximity_ties_by_depth(review_mod, tmp_path):
    # Two files under pkg/deep give pkg and pkg/deep the same count, so the
    # tie must be broken by depth: the nearer doctrine is read first.
    for rel in ("pkg/AGENTS.md", "pkg/deep/AGENTS.md"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{rel} rules", encoding="utf-8")
    diff = "+++ b/pkg/deep/mod.py\n+++ b/pkg/deep/other.py\n"

    names = review_mod.nested_guideline_paths(str(tmp_path), diff)

    assert names[0] == "pkg/deep/AGENTS.md"


def test_at_most_five_nested_doctrine_files_enter(review_mod, tmp_path):
    for index in range(8):
        directory = tmp_path / f"pkg{index}" / "sub"
        directory.mkdir(parents=True)
        (directory / "AGENTS.md").write_text(f"nested {index}", encoding="utf-8")
    diff = "".join(f"+++ b/pkg{index}/sub/x.py\n" for index in range(8))

    names = review_mod.nested_guideline_paths(str(tmp_path), diff)

    assert len(names) == review_mod.MAX_NESTED_GUIDELINES == 5
    assert names == sorted(names)


def test_the_root_agents_md_is_not_duplicated_by_the_nested_walk(review_mod, tmp_path):
    (tmp_path / "AGENTS.md").write_text("root rules", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "AGENTS.md").write_text("nested rules", encoding="utf-8")

    doctrine = review_mod.load_guidelines(str(tmp_path), "+++ b/src/mod.py\n")

    assert doctrine.count("## AGENTS.md\n") == 1
    assert "## src/AGENTS.md" in doctrine


# --- doctrine truncation: head and tail, honestly ---------------------------


def test_long_doctrine_keeps_head_and_tail_with_a_marker(review_mod, tmp_path):
    head_token = "HEAD-OF-DOCTRINE"
    tail_token = "TAIL-OF-DOCTRINE"
    filler = "z" * (review_mod.MAX_GUIDELINES_CHARS + 1000)
    (tmp_path / "AGENTS.md").write_text(
        head_token + filler + tail_token, encoding="utf-8"
    )

    doctrine = review_mod.load_guidelines(str(tmp_path), "")

    assert head_token in doctrine, "the head must survive truncation"
    assert tail_token in doctrine, "the tail (where the hard rules live) must survive"
    assert "[guidelines truncated:" in doctrine
    assert "chars omitted in the middle]" in doctrine


def test_total_guidelines_budget_omits_files_beyond_it(review_mod, tmp_path):
    # Five ~30k files are far more than the 80k total budget. The files that
    # no longer fit are omitted, each named by a marker; nothing is taken from
    # the diff to make room, because the budget is the doctrine's alone.
    files = {
        "AGENTS.md": "alpha",
        "CLAUDE.md": "bravo",
        ".github/copilot-instructions.md": "charlie",
        ".pi/AGENTS.md": "delta",
        "src/AGENTS.md": "echo",
    }
    for rel, token in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token + "x" * 29_990, encoding="utf-8")

    doctrine = review_mod.load_guidelines(str(tmp_path), "+++ b/src/mod.py\n")

    assert doctrine.count("[guidelines omitted: ") == 3
    assert review_mod.MAX_TOTAL_GUIDELINES_CHARS == 80_000
    assert (
        "[guidelines omitted: .github/copilot-instructions.md (total budget)]"
        in doctrine
    )
    assert "[guidelines omitted: .pi/AGENTS.md (total budget)]" in doctrine
    assert "[guidelines omitted: src/AGENTS.md (total budget)]" in doctrine
    # The two that fit come first and keep their content.
    assert doctrine.index("alpha") < doctrine.index("[guidelines omitted: ")
    assert doctrine.index("bravo") < doctrine.index("[guidelines omitted: ")


def test_two_files_at_the_per_file_cap_both_fit_the_total_budget(review_mod, tmp_path):
    # Two files at exactly the per-file cap plus their headings overran an 80k
    # total whose accounting ignored the headings. The per-file cap now covers
    # the whole section, so neither is omitted and the assembled doctrine --
    # headings and separators included -- stays inside the budget.
    big = "x" * review_mod.MAX_GUIDELINES_CHARS
    (tmp_path / "AGENTS.md").write_text(big, encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text(big, encoding="utf-8")

    doctrine = review_mod.load_guidelines(str(tmp_path), "")

    assert "[guidelines omitted:" not in doctrine
    assert "## AGENTS.md" in doctrine
    assert "## CLAUDE.md" in doctrine
    assert len(doctrine) <= review_mod.MAX_TOTAL_GUIDELINES_CHARS


def test_a_third_file_at_the_cap_is_omitted_with_a_marker(review_mod, tmp_path):
    big = "x" * review_mod.MAX_GUIDELINES_CHARS
    for rel in ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(big, encoding="utf-8")

    doctrine = review_mod.load_guidelines(str(tmp_path), "")

    assert doctrine.count("[guidelines omitted: ") == 1
    assert (
        "[guidelines omitted: .github/copilot-instructions.md (total budget)]"
        in doctrine
    )


def test_short_doctrine_is_not_truncated(review_mod, tmp_path):
    (tmp_path / "AGENTS.md").write_text("small doctrine", encoding="utf-8")

    doctrine = review_mod.load_guidelines(str(tmp_path), "")

    assert "small doctrine" in doctrine
    assert "truncated" not in doctrine


# --- the HTTP seam ----------------------------------------------------------


def test_good_response_returns_the_message_content(review_mod):
    body = json.dumps({"choices": [{"message": {"content": "review text"}}]})
    opener = FakeHTTPClient(body=body.encode("utf-8"))

    assert review_mod.call_openrouter("key", {"model": "m"}, opener=opener) == "review text"
    assert len(opener.requests) == 1
    assert opener.requests[0].full_url == review_mod.OPENROUTER_URL
    assert opener.requests[0].headers.get("Authorization") == "Bearer key"


def test_malformed_response_is_a_readable_error_and_nonzero(review_mod):
    opener = FakeHTTPClient(body=json.dumps({"not": "choices"}).encode("utf-8"))

    with pytest.raises(SystemExit) as excinfo:
        review_mod.call_openrouter("key", {"model": "m"}, opener=opener)

    assert "Unexpected OpenRouter response shape" in str(excinfo.value)
    assert excinfo.value.code != 0


def test_http_error_is_a_readable_error(review_mod):
    error = urllib.error.HTTPError(
        review_mod.OPENROUTER_URL,
        401,
        "Unauthorized",
        None,
        io.BytesIO(b'{"error": "bad key"}'),
    )
    opener = FakeHTTPClient(error=error)

    with pytest.raises(SystemExit) as excinfo:
        review_mod.call_openrouter("key", {"model": "m"}, opener=opener)

    assert "OpenRouter request failed: 401 Unauthorized" in str(excinfo.value)
    assert "bad key" in str(excinfo.value)


# --- main: empty diff, and the written artifact -----------------------------


def test_empty_diff_makes_no_call_and_says_so(review_mod, tmp_path, monkeypatch):
    diff_path = tmp_path / "pr.diff"
    diff_path.write_text("\n \n", encoding="utf-8")
    out = tmp_path / "review.md"

    def explode(*args, **kwargs):
        raise AssertionError("no HTTP call may happen for an empty diff")

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(review_mod, "call_openrouter", explode)
    monkeypatch.setattr(
        sys, "argv",
        ["openrouter_review.py", "--diff", str(diff_path), "--out", str(out)],
    )

    assert review_mod.main() == 0
    text = out.read_text(encoding="utf-8")
    assert "No diff content to review (empty diff)." in text
    assert review_mod.REVIEW_MARKER in text


def test_review_file_carries_model_marker_and_reviewed_commit(review_mod, tmp_path, monkeypatch):
    diff_path = tmp_path / "pr.diff"
    diff_path.write_text("+++ b/x.py\n+print('hi')\n", encoding="utf-8")
    out = tmp_path / "review.md"

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "vendor/model-x")
    monkeypatch.setattr(
        review_mod, "call_openrouter", lambda key, payload: "### Review\nLGTM."
    )
    monkeypatch.setattr(
        sys, "argv",
        [
            "openrouter_review.py", "--diff", str(diff_path), "--out", str(out),
            "--repo-root", str(tmp_path), "--commit", "abc123def",
        ],
    )

    assert review_mod.main() == 0
    text = out.read_text(encoding="utf-8")
    assert "LGTM." in text
    assert "vendor/model-x" in text
    assert "Reviewed commit: abc123def" in text
    assert review_mod.REVIEW_MARKER in text


def _workflow_steps(workflow: str) -> list[dict[str, str]]:
    """The workflow's ``steps:`` entries, each with its ``name`` and ``run``.

    PyYAML is not a declared test dependency (``constraints/test.txt`` pins
    pytest, pytest-asyncio and jsonschema; nothing else may be assumed), so
    this is a small parser for the one shape the workflow uses: a ``steps:``
    list whose items are ``- name:`` mappings with an optional literal
    ``run: |`` block. It is not a general YAML parser. It is line-based so a
    ``set -euo pipefail`` inside a run block is attributed to *that* step,
    which is the property the tests below need and a file-wide grep cannot
    express.
    """
    lines = workflow.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == "steps:"), None
    )
    if start is None:
        return []
    base = len(lines[start]) - len(lines[start].lstrip())
    steps: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    i = start + 1
    while i < len(lines):
        line = lines[i]
        indent = len(line) - len(line.lstrip())
        if line.strip() and indent <= base:
            break
        body = line.strip()
        if body.startswith("- "):
            body = body[2:]
            current = {"name": "", "run": ""}
            steps.append(current)
        if body.startswith("name:") and current is not None:
            current["name"] = body[len("name:"):].strip().strip('"').strip("'")
            i += 1
            continue
        if body.startswith("run:") and current is not None:
            run_lines: list[str] = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                nxt_indent = len(nxt) - len(nxt.lstrip())
                if nxt.strip() and nxt_indent <= indent:
                    break
                run_lines.append(nxt)
                i += 1
            current["run"] = "\n".join(run_lines)
            continue
        i += 1
    return steps


def test_the_jenkins_contract_suite_fails_loudly():
    """The replacement owner must propagate pytest failure, not mask it."""
    pipeline = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    command = ".venv/bin/python -m pytest tests/ -q"

    assert pipeline.count(command) == 1
    invocation = next(line for line in pipeline.splitlines() if command in line)
    assert "||" not in invocation
    assert "true" not in invocation


def test_retired_openrouter_automation_leaves_no_secret_bearing_replacement():
    """Jenkins must not run PR code with the retired OpenRouter secret."""
    pipeline = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")

    workflow = REPO_ROOT / ".github" / "workflows" / "openrouter-review.yml"
    assert not workflow.exists()
    assert "OPENROUTER_API_KEY" not in pipeline
    assert "openrouter_review.py" not in pipeline


def test_the_marker_remains_exact_for_manually_generated_review_output(review_mod):
    # The generator remains usable manually and its output contract is stable,
    # even though no automated workflow now performs a comment upsert.
    assert review_mod.REVIEW_MARKER == "<!-- openrouter-review -->"
