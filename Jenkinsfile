// Jenkins webhook bootstrap validated against GitHub App events.
pipeline {
    agent {
        docker {
            image 'python:3.10.12-slim'
            reuseNode true
        }
    }

    options {
        disableConcurrentBuilds(abortPrevious: true)
        timeout(time: 20, unit: 'MINUTES')
        timestamps()
    }

    stages {
        stage('Environment') {
            steps {
                sh '''
                    set -eu
                    python --version
                '''
            }
        }

        stage('Install system tools') {
            steps {
                sh '''
                    set -eu
                    apt-get update
                    apt-get install -y --no-install-recommends git
                    rm -rf /var/lib/apt/lists/*
                    getent group ci >/dev/null || groupadd -g 10001 ci
                    id ci >/dev/null 2>&1 || useradd -m -u 10001 -g 10001 ci
                    chown -R 10001:10001 "$WORKSPACE"
                    git --version
                '''
            }
        }

        stage('frozen-contract-paths') {
            when {
                changeRequest target: 'main'
            }
            steps {
                sh '''
                    set -eu
                    test -n "$CHANGE_TARGET"
                    git config --global --add safe.directory "$WORKSPACE"
                    test -n "${CHANGE_TARGET:-}"
                    test -n "${CHANGE_ID:-}"
                    test -n "${GIT_COMMIT:-}"
                    BASE_SHA="$(git rev-parse "origin/${CHANGE_TARGET}")"
                    HEAD_SHA="$(git rev-parse HEAD)"
                    test "$HEAD_SHA" = "$GIT_COMMIT"
                    git cat-file -e "$BASE_SHA^{commit}"
                    git cat-file -e "$HEAD_SHA^{commit}"
                    TRUSTED_CHECKER="$(mktemp)"
                    trap 'rm -f "$TRUSTED_CHECKER"' EXIT
                    git show "${BASE_SHA}:tools/check_frozen_paths.py" > "$TRUSTED_CHECKER"
                    chmod 0444 "$TRUSTED_CHECKER"
                    echo "Frozen contract audit: $BASE_SHA..$HEAD_SHA"
                    su ci -s /bin/sh -c "python '$TRUSTED_CHECKER' '$BASE_SHA' '$HEAD_SHA'"
                '''
            }
        }

        stage('Install test environment') {
            steps {
                sh '''
                    set -eu
                    su ci -s /bin/sh -c 'python -m venv .venv'
                    su ci -s /bin/sh -c '.venv/bin/python -m pip install --upgrade pip'
                    su ci -s /bin/sh -c '.venv/bin/python -m pip install -e ".[test]" -c constraints/test.txt'
                '''
            }
        }

        stage('contract-suite') {
            steps {
                sh "su ci -s /bin/sh -c '.venv/bin/python -m pytest tests/ -q'"
            }
        }

        stage('anchor-plugs') {
            steps {
                sh '''
                    set -eu
                    su ci -s /bin/sh -c '.venv/bin/python -m pip install -e "./plugs/motus-anchor-opentimestamps[test]"'
                    su ci -s /bin/sh -c '.venv/bin/python -m pytest plugs/motus-anchor-opentimestamps/tests -q'
                    su ci -s /bin/sh -c '.venv/bin/python -m pip install -e "./plugs/motus-attest-rfc3161[test]"'
                    su ci -s /bin/sh -c '.venv/bin/python -m pytest plugs/motus-attest-rfc3161/tests -q'
                '''
            }
        }

        stage('slo-baseline') {
            steps {
                sh '''
                    su ci -s /bin/sh -c '.venv/bin/python benchmarks/check_slo_baseline.py'
                '''
            }
        }
    }

    post {
        always {
            deleteDir()
        }
    }
}
