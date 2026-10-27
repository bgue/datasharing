def pytest_addoption(parser):
    parser.addoption("--update-golden", action="store_true", default=False,
                     help="rewrite tests/golden/*.json from the current code (same as ROTHERY_UPDATE_GOLDEN=1)")
