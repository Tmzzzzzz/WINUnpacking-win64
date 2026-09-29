"""pytest 公共配置：把 src/ 加入导入路径，并提供临时目录夹具。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    """每个用例独立的临时工作目录。"""
    d = tmp_path / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d
