import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worklog.app import create_app
from worklog.config import load_config
from worklog.store import Store


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "w.sqlite")


@pytest.fixture
def client(cfg, store):
    return create_app(cfg, store).test_client()
