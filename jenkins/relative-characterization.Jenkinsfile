// Dedicated ADR-012 cumulative-drift measurement. This is intentionally not
// part of every pull-request build: one Jenkins job loads this file from main.
pipeline {
    agent {
        docker {
            image 'python:3.10.12-slim'
            reuseNode true
        }
    }

    triggers {
        // Monday 04:17 UTC, preserving the retired Actions schedule.
        cron('17 4 * * 1')
    }

    options {
        disableConcurrentBuilds()
        timeout(time: 45, unit: 'MINUTES')
        timestamps()
        buildDiscarder(logRotator(
            daysToKeepStr: '90',
            numToKeepStr: '16',
            artifactDaysToKeepStr: '90',
            artifactNumToKeepStr: '16'
        ))
    }

    stages {
        stage('Prepare subjects') {
            steps {
                sh '''
                    set -eu
                    apt-get update
                    apt-get install -y --no-install-recommends git
                    rm -rf /var/lib/apt/lists/*
                    git config --global --add safe.directory "$WORKSPACE"
                    git fetch --no-tags origin main
                    git fetch --tags origin
                    git worktree add --detach .relative-baseline v0.6.1
                    git worktree add --detach .relative-anchor v0.6.1
                    test "$(python --version 2>&1)" = "Python 3.10.12"
                '''
            }
        }

        stage('Interleaved relative measurement') {
            steps {
                sh '''
                    set -eu
                    python benchmarks/collect_relative_baseline.py \
                        --baseline-src .relative-baseline/src \
                        --baseline-ref v0.6.1 \
                        --candidate-src src \
                        --candidate-ref main \
                        --anchor-src .relative-anchor/src \
                        --anchor-ref v0.6.1 \
                        --pairs 5 \
                        > benchmarks/relative-latest.json
                    python benchmarks/check_relative_baseline.py \
                        --advisory benchmarks/relative-latest.json
                '''
            }
        }

        stage('Preserve raw evidence') {
            steps {
                archiveArtifacts artifacts: 'benchmarks/relative-latest.json', fingerprint: true
            }
        }
