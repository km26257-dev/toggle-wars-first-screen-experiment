"""Unit tests for the Flagsmith first-screen experiment client and simulator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from flagsmith.models import DefaultFlag, ExperimentMetadata, Flag

import first_screen_app as app


class FakeFlagsmithClient:
    """In-memory test double recording experiment flag lookups and tracked events."""

    def __init__(self, flags_by_user: Mapping[str, Flag | DefaultFlag]) -> None:
        self._flags_by_user = dict(flags_by_user)
        self.tracked_events: list[dict[str, Any]] = []
        self.flushed_count = 0
        self.closed = False

    def get_experiment_flag(
        self,
        feature_name: str,
        identifier: str,
        traits: Optional[Mapping[str, Any]] = None,
    ) -> DefaultFlag | Flag:
        assert feature_name == app.FEATURE_NAME
        return self._flags_by_user[identifier]

    def track_event(
        self,
        event: str,
        identifier: Optional[str] = None,
        value: Optional[str | int | float | bool] = None,
        traits: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.tracked_events.append(
            {
                "event": event,
                "identifier": identifier,
                "value": value,
                "traits": traits,
                "metadata": dict(metadata) if metadata else None,
            }
        )

    def flush_events(self) -> None:
        self.flushed_count += 1

    def close(self) -> None:
        self.closed = True


def _make_flag(
    value: str,
    enabled: bool = True,
    variant: Optional[str] = None,
    in_experiment: bool = True,
) -> Flag:
    return Flag(
        feature_id=306544,
        feature_name=app.FEATURE_NAME,
        enabled=enabled,
        value=value,
        variant=variant,
        experiment=ExperimentMetadata(
            id=1,
            name="First Screen Blank vs Sample Data",
            in_experiment=in_experiment,
        ),
    )


def test_resolve_environment_key_prefers_env_and_falls_back_to_config(
    tmp_path: Path, monkeypatch: Any
) -> None:
    config_file = tmp_path / "flagsmith.json"
    config_file.write_text(json.dumps({"project": 56725, "environment": "from_file"}), encoding="utf-8")

    monkeypatch.delenv("FLAGSMITH_ENVIRONMENT_KEY", raising=False)
    monkeypatch.delenv("FLAGSMITH_ENVIRONMENT", raising=False)
    assert app.resolve_environment_key(config_file) == "from_file"

    monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "from_env")
    assert app.resolve_environment_key(config_file) == "from_env"


def test_evaluate_and_render_blank_and_sample_data_modes() -> None:
    client = FakeFlagsmithClient(
        {
            "u_blank": _make_flag("blank", enabled=True, variant="control"),
            "u_sample": _make_flag("sample data", enabled=True, variant="Variant_1"),
            "u_disabled": _make_flag("sample data", enabled=False, variant=None, in_experiment=False),
        }
    )

    blank_decision = app.evaluate_first_screen(client, "u_blank")
    blank_ui = app.render_first_screen(blank_decision)
    assert blank_decision.mode == app.MODE_BLANK
    assert blank_ui["screen"] == "blank"
    assert blank_ui["items"] == []

    sample_decision = app.evaluate_first_screen(client, "u_sample")
    sample_ui = app.render_first_screen(sample_decision)
    assert sample_decision.mode == app.MODE_SAMPLE_DATA
    assert sample_ui["screen"] == "sample_data"
    assert len(sample_ui["items"]) == 3

    disabled_decision = app.evaluate_first_screen(client, "u_disabled")
    assert disabled_decision.mode == app.MODE_BLANK
    assert disabled_decision.in_experiment is False


def test_track_main_action_clicked_records_expected_event() -> None:
    client = FakeFlagsmithClient({})
    app.track_main_action_clicked(client, user_id="u_1", mode=app.MODE_SAMPLE_DATA)
    assert client.tracked_events == [
        {
            "event": "main_action_clicked",
            "identifier": "u_1",
            "value": 1,
            "traits": None,
            "metadata": {"screen_mode": "sample data"},
        }
    ]


def test_simulate_traffic_tracks_conversions_and_flushes() -> None:
    flags = {
        f"sim_user_{i:03d}": _make_flag(
            app.MODE_SAMPLE_DATA if i % 2 == 0 else app.MODE_BLANK,
            enabled=True,
            variant="Variant_1" if i % 2 == 0 else "control",
            in_experiment=True,
        )
        for i in range(1, 11)
    }
    client = FakeFlagsmithClient(flags)

    summary = app.simulate_traffic(
        client,
        num_users=10,
        conversion_rates={app.MODE_BLANK: 0.0, app.MODE_SAMPLE_DATA: 1.0},
    )

    assert summary["total_users"] == 10
    assert summary["enrolled_users"] == 10
    assert summary["variants"][app.MODE_BLANK] == {
        "exposures": 5,
        "conversions": 0,
        "conversion_rate": 0.0,
    }
    assert summary["variants"][app.MODE_SAMPLE_DATA] == {
        "exposures": 5,
        "conversions": 5,
        "conversion_rate": 1.0,
    }
    assert len(client.tracked_events) == 5
    assert all(e["event"] == "main_action_clicked" for e in client.tracked_events)
    assert client.flushed_count == 1
