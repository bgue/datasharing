def pytest_addoption(parser):
    parser.addoption("--update-golden", action="store_true", default=False,
                     help="rewrite tests/golden/*.json from the current code (same as ROTHERY_UPDATE_GOLDEN=1)")


import pytest


@pytest.fixture(autouse=True)
def fresh_trials():
    """Trial counts live in a process-level counter (tracking.PROCESS_TRIALS); give every test a clean one."""
    from screenlab.tracking import reset_process_trials
    reset_process_trials()
    yield
