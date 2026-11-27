"""Flagsmith first-screen experiment client, traffic simulator, and local demo server."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from functools import partial
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol
from urllib.parse import parse_qs, urlparse

from flagsmith import Flagsmith
from flagsmith.analytics import DEFAULT_EVENT_API_URL, EventProcessorConfig
from flagsmith.flagsmith import DEFAULT_API_URL
from flagsmith.models import DefaultFlag, Flag

FEATURE_NAME = "first_screen_mode"
CONVERSION_EVENT_NAME = "main_action_clicked"
MODE_BLANK = "blank"
MODE_SAMPLE_DATA = "sample data"

DEFAULT_CONVERSION_RATES: dict[str, float] = {
    MODE_BLANK: 0.25,
    MODE_SAMPLE_DATA: 0.55,
}


class FlagsmithClientProtocol(Protocol):
    """Minimal protocol implemented by Flagsmith and test doubles."""

    def get_experiment_flag(
        self,
        feature_name: str,
        identifier: str,
        traits: Optional[Mapping[str, Any]] = None,
    ) -> DefaultFlag | Flag: ...

    def track_event(
        self,
        event: str,
        identifier: Optional[str] = None,
        value: Optional[str | int | float | bool] = None,
        traits: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None: ...

    def flush_events(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class FirstScreenDecision:
    """Evaluated first-screen experiment assignment for a user."""

    user_id: str
    enabled: bool
    mode: str
    variant: Optional[str]
    in_experiment: bool
    experiment_id: Optional[int]


@dataclass(frozen=True)
class VariantStats:
    """Simulated exposure and conversion counts for a single variant."""

    exposures: int
    conversions: int

    @property
    def conversion_rate(self) -> float:
        return self.conversions / self.exposures if self.exposures else 0.0


def resolve_environment_key(config_path: Optional[Path] = None) -> str:
    """Resolve the Flagsmith environment key from env vars or flagsmith.json."""
    if env_key := os.environ.get("FLAGSMITH_ENVIRONMENT_KEY") or os.environ.get("FLAGSMITH_ENVIRONMENT"):
        return env_key.strip()

    candidate = config_path or (Path(__file__).resolve().parent / "flagsmith.json")
    if candidate.exists():
        data = json.loads(candidate.read_text(encoding="utf-8"))
        if env_key := data.get("environment"):
            return str(env_key).strip()

    raise RuntimeError(
        "Flagsmith environment key not found. Set FLAGSMITH_ENVIRONMENT_KEY or bind flagsmith.json."
    )


def create_flagsmith_client(
    environment_key: Optional[str] = None,
    api_url: Optional[str] = None,
    events_api_url: Optional[str] = None,
    flush_interval_seconds: float = 2.0,
) -> Flagsmith:
    """Create a Flagsmith SDK client configured for experiment exposure and event tracking."""
    key = environment_key or resolve_environment_key()
    resolved_api_url = api_url or os.environ.get("FLAGSMITH_API_URL") or DEFAULT_API_URL
    resolved_events_url = (
        events_api_url or os.environ.get("FLAGSMITH_EVENTS_API_URL") or DEFAULT_EVENT_API_URL
    )
    return Flagsmith(
        environment_key=key,
        api_url=resolved_api_url,
        enable_events=True,
        event_processor_config=EventProcessorConfig(
            events_api_url=resolved_events_url,
            flush_interval_seconds=flush_interval_seconds,
        ),
    )


def normalize_screen_mode(value: Any, enabled: bool) -> str:
    """Normalize raw flag values to a supported first-screen mode."""
    if not enabled:
        return MODE_BLANK
    if isinstance(value, str) and value.strip().lower() == MODE_SAMPLE_DATA:
        return MODE_SAMPLE_DATA
    return MODE_BLANK


def evaluate_first_screen(
    client: FlagsmithClientProtocol,
    user_id: str,
    traits: Optional[Mapping[str, Any]] = None,
) -> FirstScreenDecision:
    """Evaluate `first_screen_mode` for `user_id` and record an exposure when enrolled."""
    flag = client.get_experiment_flag(FEATURE_NAME, identifier=user_id, traits=traits)
    mode = normalize_screen_mode(getattr(flag, "value", MODE_BLANK), bool(flag.enabled))
    experiment = getattr(flag, "experiment", None)
    return FirstScreenDecision(
        user_id=user_id,
        enabled=bool(flag.enabled),
        mode=mode,
        variant=getattr(flag, "variant", None),
        in_experiment=bool(experiment and experiment.in_experiment),
        experiment_id=experiment.id if experiment else None,
    )


def render_first_screen(decision: FirstScreenDecision) -> dict[str, Any]:
    """Return UI payload for the user's evaluated first-screen experience."""
    if decision.mode == MODE_SAMPLE_DATA:
        return {
            "screen": "sample_data",
            "mode": decision.mode,
            "headline": "Explore with sample project data",
            "subtitle": "Pre-populated templates help you try workflows immediately.",
            "items": [
                {"id": "sample-1", "title": "Q4 Onboarding Funnel", "status": "Active"},
                {"id": "sample-2", "title": "Retention Cohort Overview", "status": "Draft"},
                {"id": "sample-3", "title": "Activation Checklist", "status": "Ready"},
            ],
            "cta_label": "Use Sample Workspace",
            "conversion_event": CONVERSION_EVENT_NAME,
        }

    return {
        "screen": "blank",
        "mode": decision.mode,
        "headline": "Start from a clean workspace",
        "subtitle": "Create your first item from scratch with zero clutter.",
        "items": [],
        "cta_label": "Create First Item",
        "conversion_event": CONVERSION_EVENT_NAME,
    }


def track_main_action_clicked(
    client: FlagsmithClientProtocol,
    user_id: str,
    mode: str,
    traits: Optional[Mapping[str, Any]] = None,
) -> None:
    """Record the `main_action_clicked` conversion event for `user_id`."""
    client.track_event(
        event=CONVERSION_EVENT_NAME,
        identifier=user_id,
        value=1,
        traits=traits,
        metadata={"screen_mode": mode},
    )


def _deterministic_bucket(user_id: str) -> float:
    """Map a user ID deterministically into [0.0, 1.0) for reproducible simulation."""
    digest = hashlib.sha256(user_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def simulate_traffic(
    client: FlagsmithClientProtocol,
    num_users: int = 40,
    user_prefix: str = "sim_user",
    conversion_rates: Optional[Mapping[str, float]] = None,
    flush: bool = True,
) -> dict[str, Any]:
    """Simulate `num_users` evaluating `first_screen_mode` and clicking the main action."""
    rates = dict(DEFAULT_CONVERSION_RATES)
    if conversion_rates:
        rates.update(conversion_rates)

    counts: dict[str, dict[str, int]] = {
        MODE_BLANK: {"exposures": 0, "conversions": 0},
        MODE_SAMPLE_DATA: {"exposures": 0, "conversions": 0},
    }
    enrolled_users = 0
    users_sample: list[dict[str, Any]] = []

    for idx in range(1, num_users + 1):
        user_id = f"{user_prefix}_{idx:03d}"
        decision = evaluate_first_screen(client, user_id=user_id)
        if decision.in_experiment:
            enrolled_users += 1

        bucket = counts.setdefault(decision.mode, {"exposures": 0, "conversions": 0})
        bucket["exposures"] += 1

        converted = _deterministic_bucket(user_id) < rates.get(decision.mode, 0.25)
        if converted:
            track_main_action_clicked(client, user_id=user_id, mode=decision.mode)
            bucket["conversions"] += 1

        if idx <= 6:
            users_sample.append(
                {
                    "user_id": user_id,
                    "mode": decision.mode,
                    "variant": decision.variant,
                    "in_experiment": decision.in_experiment,
                    "converted": converted,
                }
            )

    if flush:
        client.flush_events()

    summary_variants = {
        mode: {
            "exposures": stats["exposures"],
            "conversions": stats["conversions"],
            "conversion_rate": round(
                VariantStats(stats["exposures"], stats["conversions"]).conversion_rate, 4
            ),
        }
        for mode, stats in counts.items()
    }
    return {
        "feature": FEATURE_NAME,
        "conversion_event": CONVERSION_EVENT_NAME,
        "total_users": num_users,
        "enrolled_users": enrolled_users,
        "variants": summary_variants,
        "sample_users": users_sample,
    }


class DemoHTTPHandler(BaseHTTPRequestHandler):
    """Serve the local HTML/JS demo page and Flagsmith-backed JSON endpoints."""

    def __init__(
        self,
        *args: Any,
        flagsmith_client: FlagsmithClientProtocol,
        html_path: Path,
        **kwargs: Any,
    ) -> None:
        self._client = flagsmith_client
        self._html_path = html_path
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            html = self._html_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        if parsed.path == "/api/screen":
            params = parse_qs(parsed.query)
            user_id = (params.get("user_id") or ["demo_user_1"])[0].strip() or "demo_user_1"
            decision = evaluate_first_screen(self._client, user_id=user_id)
            self._client.flush_events()
            screen = render_first_screen(decision)
            self._send_json(
                HTTPStatus.OK,
                {"decision": asdict(decision), "screen": screen},
            )
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/track":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON body"})
            return

        user_id = str(data.get("user_id") or "demo_user_1").strip() or "demo_user_1"
        mode = str(data.get("mode") or MODE_BLANK)
        track_main_action_clicked(self._client, user_id=user_id, mode=mode)
        self._client.flush_events()
        self._send_json(
            HTTPStatus.OK,
            {"tracked": CONVERSION_EVENT_NAME, "user_id": user_id, "mode": mode},
        )


def serve_demo(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the local HTML/JS demo server backed by the Flagsmith Python SDK."""
    client = create_flagsmith_client()
    html_path = Path(__file__).resolve().parent / "demo" / "index.html"
    handler = partial(DemoHTTPHandler, flagsmith_client=client, html_path=html_path)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Serving first-screen demo at http://{host}:{port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="First-screen Flagsmith experiment runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sim_parser = subparsers.add_parser("simulate", help="Simulate user exposures and clicks")
    sim_parser.add_argument("--users", type=int, default=40, help="Number of users to simulate")
    sim_parser.add_argument("--prefix", default="sim_user", help="Prefix for simulated user IDs")

    eval_parser = subparsers.add_parser("evaluate", help="Evaluate a single user and render UI")
    eval_parser.add_argument("--user-id", required=True, help="User identifier")
    eval_parser.add_argument("--click", action="store_true", help="Also track main_action_clicked")

    serve_parser = subparsers.add_parser("serve", help="Run the local HTML/JS demo server")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()

    if args.command == "serve":
        serve_demo(host=args.host, port=args.port)
        return

    client = create_flagsmith_client()
    try:
        if args.command == "simulate":
            report = simulate_traffic(client, num_users=args.users, user_prefix=args.prefix)
            print(json.dumps(report, indent=2))
        elif args.command == "evaluate":
            decision = evaluate_first_screen(client, user_id=args.user_id)
            if args.click:
                track_main_action_clicked(client, user_id=args.user_id, mode=decision.mode)
            client.flush_events()
            print(
                json.dumps(
                    {"decision": asdict(decision), "screen": render_first_screen(decision)},
                    indent=2,
                )
            )
    finally:
        client.close()


if __name__ == "__main__":
    main()
