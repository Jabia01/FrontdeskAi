"""Checkers for the live eval, and their unit tests.

The checkers live under `tests/` because they are tested like anything else -
`pyproject.toml` sets `testpaths = ["tests"]`, so `tests/evals/test_checks.py`
runs in CI as pure functions over fixture strings, with no model and no
network.

What *uses* them lives in `scripts/eval_niches.py`, deliberately outside
`testpaths`, because that is the thing that holds an API key. The guard class
`TestTheSuiteCannotCallAProvider` in `tests/test_deploy_readiness.py` stays
true by construction rather than by remembering to skip a marker.
"""
