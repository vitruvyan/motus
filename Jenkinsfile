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

        stage('anchor plug tests') {
            steps {
                sh '''
                    set -eu
                    su ci -s /bin/sh -c '.venv/bin/python -m pip install -e "./plugs/motus-anchor-opentimestamps[test]"'
                    su ci -s /bin/sh -c '.venv/bin/python -m pytest plugs/motus-anchor-opentimestamps/tests -q'
                '''
            }
        }

        stage('RFC3161 plug tests') {
            steps {
                sh '''
                    set -eu
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
