import pytest
from fastapi.testclient import TestClient

from assistant.engine import Engine
from assistant.planner import RuleBasedPlanner
from mock_api import main


@pytest.fixture()
def client():
    main.reset()
    return TestClient(main.app)


@pytest.fixture()
def engine(client, tmp_path):
    return Engine(client, RuleBasedPlanner(), db_path=str(tmp_path / "runs.db"))
