from datetime import datetime, timezone

import pytest

from app.ws.depth import DepthGapError, DepthSynchronizer


def _create_snapshot() -> dict:
    return {
        "lastUpdateId": 100,
        "bids": [["100.0", "1.0"]],
        "asks": [["101.0", "2.0"]],
    }


def test_depth_synchronizer_applies_initial_update() -> None:
    sync = DepthSynchronizer()
    sync.load_snapshot(_create_snapshot())

    update = sync.apply_update(
        {
            "e": "depthUpdate",
            "E": 1717440000000,
            "U": 101,
            "u": 102,
            "b": [["99.5", "0.5"]],
            "a": [["101.5", "3.0"]],
        }
    )

    assert update is not None
    assert update.lastUpdateId == 102
    assert update.bids[0].price == pytest.approx(99.5)
    assert update.bids[0].qty == pytest.approx(0.5)
    assert update.asks[0].price == pytest.approx(101.5)
    assert update.ts == datetime.fromtimestamp(1717440000000 / 1000, tz=timezone.utc)


def test_depth_synchronizer_skips_outdated_update() -> None:
    sync = DepthSynchronizer()
    sync.load_snapshot(_create_snapshot())

    result = sync.apply_update(
        {
            "e": "depthUpdate",
            "E": 1717440000000,
            "U": 90,
            "u": 95,
            "b": [["99.0", "0.3"]],
            "a": [],
        }
    )

    assert result is None


def test_depth_synchronizer_detects_gap_and_requires_resync() -> None:
    sync = DepthSynchronizer()
    sync.load_snapshot(_create_snapshot())

    # bring synchronizer to ready state
    sync.apply_update(
        {
            "e": "depthUpdate",
            "E": 1717440000000,
            "U": 101,
            "u": 102,
            "b": [],
            "a": [],
        }
    )

    with pytest.raises(DepthGapError):
        sync.apply_update(
            {
                "e": "depthUpdate",
                "E": 1717440000500,
                "U": 110,
                "u": 111,
                "b": [],
                "a": [],
            }
        )


def test_depth_synchronizer_handles_removals() -> None:
    sync = DepthSynchronizer()
    sync.load_snapshot(_create_snapshot())
    sync.apply_update(
        {
            "e": "depthUpdate",
            "E": 1717440000000,
            "U": 101,
            "u": 102,
            "b": [],
            "a": [],
        }
    )

    update = sync.apply_update(
        {
            "e": "depthUpdate",
            "E": 1717440000200,
            "U": 103,
            "u": 104,
            "b": [["100.0", "0"]],
            "a": [["101.0", "0"]],
        }
    )

    assert update is not None
    assert update.bids[0].qty == 0
    assert update.asks[0].qty == 0
