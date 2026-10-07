pipeline {
  agent any
  options { timestamps() }
  environment { VENV = '.venv' }
  stages {
    stage('Install') {
      steps {
        sh 'python3 -m venv $VENV && $VENV/bin/pip install -q -r requirements.txt'
      }
    }
    stage('Lint') {
      steps { sh '$VENV/bin/python -m ruff check .' }
    }
    stage('Test') {
      steps { sh '$VENV/bin/python -m pytest --cov=recon --cov-report=term --cov-report=xml --junitxml=reports/junit.xml' }
    }
    stage('Demo reconciliation') {
      steps {
        sh '$VENV/bin/python scripts/seed_demo.py'
        // The demo data has planted defects, so FAIL is expected: only this stage may fail without failing the build.
        catchError(buildResult: 'SUCCESS', stageResult: 'UNSTABLE') {
          sh '$VENV/bin/python -m recon run suites/demo_migration.yaml --fail-on-mismatch'
        }
      }
    }
  }
  post {
    always { archiveArtifacts artifacts: 'reports/**', allowEmptyArchive: true }
  }
}
