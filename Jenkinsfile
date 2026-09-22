/* Temporary 0.17.0 release-characterization pipeline.
 * Exists only on release/0.17.0 and is restored to main before the release PR.
 * Three evidence commits = three independent Jenkins dispatches.
 */
pipeline {
    agent {
        docker {
            image 'python:3.10.12-slim'
            reuseNode true
        }
    }

    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds(abortPrevious: true)
        timeout(time: 45, unit: 'MINUTES')
        timestamps()
    }

    stages {
        stage('Install Git') {
            steps {
                sh '''
                    set -eu
                    apt-get update
                    apt-get install -y --no-install-recommends git
                    rm -rf /var/lib/apt/lists/*
                    git --version
                    python --version
                '''
            }
        }

        stage('Checkout release candidate and baselines') {
            steps {
                checkout([
                    $class: 'GitSCM',
                    branches: [[name: 'origin/release/0.17.0']],
                    doGenerateSubmoduleConfigurations: false,
                    extensions: [[$class: 'CloneOption', depth: 0, noTags: false, shallow: false, timeout: 10]],
                    userRemoteConfigs: [[
                        credentialsId: 'github-vitruvyan-jenkins-app',
                        refspec: '+refs/heads/release/0.17.0:refs/remotes/origin/release/0.17.0 +refs/tags/v0.15.0:refs/tags/v0.15.0 +refs/tags/v0.6.1:refs/tags/v0.6.1',
                        url: 'https://github.com/vitruvyan/motus.git'
                    ]]
                ])
                sh '''
                    set -eu
                    test "$(git rev-parse HEAD)" = "$(git rev-parse origin/release/0.17.0)"
                    test "$(python - <<'PY'
import ast
from pathlib import Path
tree = ast.parse(Path("src/vitruvyan_motus/__init__.py").read_text())
for node in tree.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets):
        print(ast.literal_eval(node.value))
        break
PY
)" = "0.17.0"
                '''
            }
        }

        stage('Characterize one independent dispatch') {
            steps {
                sh '''
                    set -eu
                    mkdir -p benchmarks/relative-0.17.0 .ci
                    COUNT=$(find benchmarks/relative-0.17.0 -maxdepth 1 -type f -name 'job-*.json' | wc -l | tr -d ' ')
                    if [ "$COUNT" -ge 3 ]; then
                        echo "Three release characterization jobs already committed."
                        python benchmarks/check_relative_baseline.py benchmarks/relative-0.17.0/*.json
                        python benchmarks/check_slo_baseline.py
                        exit 0
                    fi

                    JOB=$((COUNT + 1))
                    BASELINE_DIR="$WORKSPACE/.ci/release-baseline"
                    ANCHOR_DIR="$WORKSPACE/.ci/release-anchor"
                    cleanup() {
                        git worktree remove --force "$BASELINE_DIR" >/dev/null 2>&1 || true
                        git worktree remove --force "$ANCHOR_DIR" >/dev/null 2>&1 || true
                    }
                    trap cleanup EXIT HUP INT TERM
                    cleanup

                    git worktree add --detach "$BASELINE_DIR" refs/tags/v0.15.0
                    git worktree add --detach "$ANCHOR_DIR" refs/tags/v0.6.1

                    CANDIDATE_SHA=$(git rev-parse HEAD)
                    python benchmarks/collect_relative_baseline.py \
                        --baseline-src "$BASELINE_DIR/src" \
                        --baseline-ref v0.15.0 \
                        --candidate-src src \
                        --candidate-ref "$CANDIDATE_SHA" \
                        --anchor-src "$ANCHOR_DIR/src" \
                        --anchor-ref v0.6.1 \
                        --pairs 5 \
                        > "benchmarks/relative-0.17.0/job-$JOB.json"

                    if [ "$JOB" -eq 1 ]; then
                        python benchmarks/collect_motus_baseline.py 5 \
                            > benchmarks/candidate-v0.17.0-epyc-py310.json
                        python benchmarks/check_slo_baseline.py
                    fi

                    if [ "$JOB" -lt 3 ]; then
                        python benchmarks/check_relative_baseline.py --advisory \
                            benchmarks/relative-0.17.0/*.json
                    else
                        python benchmarks/check_relative_baseline.py \
                            benchmarks/relative-0.17.0/*.json
                    fi

                    cleanup
                    trap - EXIT HUP INT TERM

                    git config user.name "Vitruvyan Jenkins CI"
                    git config user.email "ci@vitruvyan.invalid"
                    git add "benchmarks/relative-0.17.0/job-$JOB.json"
                    if [ "$JOB" -eq 1 ]; then
                        git add benchmarks/candidate-v0.17.0-epyc-py310.json
                    fi
                    git commit -m "release: record 0.17.0 characterization job $JOB"
                '''
                step([
                    $class: 'GitPublisher',
                    branchesToPush: [[
                        branchName: 'release/0.17.0',
                        targetRepoName: 'origin'
                    ]],
                    pushOnlyIfSuccess: true
                ])
            }
        }
    }

    post {
        always {
            deleteDir()
        }
    }
}
