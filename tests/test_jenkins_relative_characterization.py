"""Repository contracts for the dedicated ADR-012 Jenkins schedule."""

from pathlib import Path


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
