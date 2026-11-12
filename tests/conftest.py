import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worklog.config import load_config


@pytest.fixture
def cfg():
    return load_config()
