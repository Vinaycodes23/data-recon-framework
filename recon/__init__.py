"""Data reconciliation framework: compare a source dataset against a target dataset."""

from recon.config import Suite, TestConfig, load_suite, save_suite
from recon.engine import TestResult, run_test
from recon.runner import SuiteRun, run_suite

__all__ = [
    "Suite",
    "SuiteRun",
    "TestConfig",
    "TestResult",
    "load_suite",
    "run_suite",
    "run_test",
    "save_suite",
]
__version__ = "1.0.0"
