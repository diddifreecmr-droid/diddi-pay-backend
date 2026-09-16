"""Static safety checks for the optional OBS-3 monitoring stack."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_grafana_dashboard_is_valid_and_has_unique_panels() -> None:
    dashboard = json.loads(
        (ROOT / "deploy/observability/grafana/dashboards/diddipay-overview.json").read_text()
    )
    panels = dashboard["panels"]

    assert dashboard["uid"] == "diddipay-overview"
    assert len(panels) >= 10
    assert len({panel["id"] for panel in panels}) == len(panels)
    assert all(panel["datasource"]["uid"] == "diddipay-prometheus" for panel in panels)


def test_prometheus_uses_external_token_and_loads_alerts() -> None:
    config = (ROOT / "deploy/observability/prometheus/prometheus.yml").read_text()
    alerts = (ROOT / "deploy/observability/prometheus/alerts.yml").read_text()

    assert "credentials_file: /run/secrets/diddipay_metrics_token" in config
    assert "metrics_path: /internal/metrics" in config
    assert "/etc/prometheus/alerts.yml" in config
    assert "DiddiPayMetricsTargetDown" in alerts
    assert "DiddiPayHighHttp5xxRate" in alerts
    assert "DiddiPayProviderTransportErrors" in alerts
    assert "DiddiPayOutboxDeadLetters" in alerts


def test_monitoring_images_are_pinned_and_ports_are_local_only() -> None:
    compose = (ROOT / "docker-compose.observability.yml").read_text()

    assert "prom/prometheus:v3.12.0" in compose
    assert "grafana/grafana:13.2.1" in compose
    assert '127.0.0.1:${PROMETHEUS_PORT:-49090}:9090' in compose
    assert '127.0.0.1:${GRAFANA_PORT:-43000}:3000' in compose
    assert "GRAFANA_ADMIN_PASSWORD must be set" in compose
