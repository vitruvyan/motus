"""Repository contracts for the dedicated ADR-012 Jenkins schedule."""

from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "Jenkinsfile.relative-characterization"


def test_weekly_relative_characterization_is_a_dedicated_scheduled_pipeline():
    source = PIPELINE.read_text(encoding="utf-8")

    assert "image 'python:3.10.12-slim'" in source
    assert "apt-get install -y --no-install-recommends git" in source
    assert "cron('TZ=UTC\\n17 4 * * 1')" in source
    assert "BASELINE_REF=v0.6.1" in source
    assert "--anchor-ref \"$BASELINE_REF\"" in source
    assert "--pairs 5" in source
    assert "benchmarks/collect_relative_baseline.py" in source
    assert "benchmarks/check_relative_baseline.py" in source
    assert "--advisory" in source


def test_weekly_job_preserves_raw_evidence_with_bounded_retention():
    source = PIPELINE.read_text(encoding="utf-8")

    assert "artifactDaysToKeepStr: '90'" in source
    assert "artifactNumToKeepStr: '20'" in source
    assert "archiveArtifacts allowEmptyArchive: false" in source
    assert "reports/motus-relative-latest.json" in source


def test_weekly_job_is_discoverable_and_not_part_of_every_pr_build():
    documentation = (ROOT / "docs" / "JENKINS.md").read_text(encoding="utf-8")
    pr_pipeline = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")

    assert "Motus / weekly-relative-characterization" in documentation
    assert "Jenkinsfile.relative-characterization" in documentation
    assert "collect_relative_baseline.py" not in pr_pipeline



def test_weekly_checkout_resolves_the_fetched_remote_main(tmp_path):
    source = PIPELINE.read_text(encoding="utf-8")
    selector = source.split("branches: [[name: '", 1)[1].split("'", 1)[0]
    refspec = re.search(r"refspec: '([^']+)'", source).group(1).split()[0]

    upstream = tmp_path / "upstream"
    bare = tmp_path / "origin.git"
    workspace = tmp_path / "workspace"
    upstream.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=upstream, check=True)
    (upstream / "proof").write_text("trusted main\n", encoding="utf-8")
    subprocess.run(["git", "add", "proof"], cwd=upstream, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Motus CI", "-c", "user.email=ci@invalid.example",
         "commit", "-qm", "trusted main"],
        cwd=upstream,
        check=True,
    )
    subprocess.run(["git", "clone", "-q", "--bare", str(upstream), str(bare)], check=True)
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=workspace, check=True)
    subprocess.run(["git", "fetch", "-q", "origin", refspec], cwd=workspace, check=True)
    subprocess.run(["git", "checkout", "-q", selector], cwd=workspace, check=True)

    assert (workspace / "proof").read_text(encoding="utf-8") == "trusted main\n"


def test_advisory_single_job_reports_cumulative_ratio_before_succeeding():
    evidence = ROOT / "benchmarks" / "relative-0.14.0" / "job-1.json"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "benchmarks" / "check_relative_baseline.py"),
         "--advisory", str(evidence)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "Cumulative budget" in completed.stdout
    assert "Relative baseline gate: ADVISORY" in completed.stdout
    assert "1 job(s); at least 3 independent dispatches" in completed.stdout
