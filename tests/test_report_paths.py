"""`runs.report_path` must stay the repo-relative posix form of §8.3.

Regression guard: the dev runner absolutises REPORT_DIR before starting the service, and
the path was previously built by interpolating that value — which stored a machine-specific
path with mixed separators the moment a `.env` existed.
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pdf import render

IL = ZoneInfo("Asia/Jerusalem")
NOON = datetime(2026, 7, 30, 15, 15, tzinfo=IL)


@pytest.fixture
def at_noon(monkeypatch, clean_env):
    """Freeze the clock so the date/time partition is deterministic."""
    return NOON


def test_relative_report_dir(monkeypatch, at_noon):
    monkeypatch.setenv("REPORT_DIR", "reports")
    abs_path, rel = render._report_paths(at_noon)
    assert rel == "reports/2026-07-30/1515/report.pdf"
    assert os.path.isabs(abs_path)


def test_absolute_report_dir_inside_the_repo(monkeypatch, at_noon):
    """What `npm run dev` actually passes — this is the case that regressed."""
    monkeypatch.setenv("REPORT_DIR", os.path.join(render._REPO_ROOT, "reports"))
    abs_path, rel = render._report_paths(at_noon)
    assert rel == "reports/2026-07-30/1515/report.pdf"
    assert "\\" not in rel, "the stored path must be posix, not OS-native"
    assert os.path.isabs(abs_path)


def test_unset_report_dir_falls_back_to_config(monkeypatch, at_noon):
    monkeypatch.delenv("REPORT_DIR", raising=False)
    _, rel = render._report_paths(at_noon)
    assert rel.startswith("reports/")
    assert rel.endswith("/report.pdf")


def test_report_dir_outside_the_repo_keeps_an_absolute_path(monkeypatch, at_noon, tmp_path):
    """No repo-relative form exists, so a `../..` chain would be worse than absolute."""
    monkeypatch.setenv("REPORT_DIR", str(tmp_path))
    abs_path, rel = render._report_paths(at_noon)
    assert not rel.startswith("..")
    assert rel.endswith("/2026-07-30/1515/report.pdf")
    assert os.path.isabs(abs_path)


def test_partitioned_by_israeli_wall_clock(monkeypatch):
    """§8.3/§11.2: stored timestamps are UTC, but the path is local wall-clock."""
    monkeypatch.setenv("REPORT_DIR", "reports")
    # 23:30 UTC on the 30th is 02:30 Israel on the 31st — the path must say the 31st.
    late = datetime(2026, 7, 30, 23, 30, tzinfo=ZoneInfo("UTC"))
    _, rel = render._report_paths(late)
    assert rel == "reports/2026-07-31/0230/report.pdf"
