"""Static safety checks for the optional OBS-3 monitoring stack."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_observability_yaml_files_are_valid() -> None:
    files = [
        ROOT / "docker-compose.observability.yml",
        ROOT / "deploy/observability/prometheus/prometheus.yml",
        ROOT / "deploy/observability/prometheus/alerts.yml",
        ROOT / "deploy/observability/prometheus/slo-rules.yml",
        ROOT / "deploy/observability/alertmanager/alertmanager.yml",
        ROOT / "deploy/observability/otel-collector/config.yml",
        ROOT / "deploy/observability/tempo/tempo.yml",
        ROOT / "deploy/observability/grafana/provisioning/datasources/prometheus.yml",
        ROOT / "deploy/observability/grafana/provisioning/datasources/tempo.yml",
        ROOT / "deploy/observability/grafana/provisioning/dashboards/diddipay.yml",
    ]

    for path in files:
        assert yaml.safe_load(path.read_text()), path


def test_grafana_dashboard_is_valid_and_has_unique_panels() -> None:
    dashboard = json.loads(
        (ROOT / "deploy/observability/grafana/dashboards/diddipay-overview.json").read_text()
    )
    panels = dashboard["panels"]

    assert dashboard["uid"] == "diddipay-overview"
    assert len(panels) >= 10
    assert len({panel["id"] for panel in panels}) == len(panels)
    assert all(panel["datasource"]["uid"] == "diddipay-prometheus" for panel in panels)


def test_slo_dashboard_is_valid_and_uses_recorded_metrics() -> None:
    dashboard = json.loads(
        (ROOT / "deploy/observability/grafana/dashboards/diddipay-slo.json").read_text()
    )
    panels = dashboard["panels"]
    expressions = " ".join(target["expr"] for panel in panels for target in panel["targets"])

    assert dashboard["uid"] == "diddipay-slo"
    assert len(panels) >= 6
    assert len({panel["id"] for panel in panels}) == len(panels)
    assert all(panel["datasource"]["uid"] == "diddipay-prometheus" for panel in panels)
    assert "diddipay:http_5xx_ratio:rate5m" in expressions
    assert "diddipay:webhook_failure_ratio:rate15m" in expressions
    assert "diddipay:callback_failure_ratio:rate15m" in expressions


def test_prometheus_uses_external_token_and_loads_alerts() -> None:
    config = (ROOT / "deploy/observability/prometheus/prometheus.yml").read_text()
    alerts = (ROOT / "deploy/observability/prometheus/alerts.yml").read_text()

    assert "credentials_file: /run/secrets/diddipay_metrics_token" in config
    assert "metrics_path: /internal/metrics" in config
    assert "/etc/prometheus/alerts.yml" in config
    assert "/etc/prometheus/slo-rules.yml" in config
    assert 'targets: ["alertmanager:9093"]' in config
    assert "DiddiPayMetricsTargetDown" in alerts
    assert "DiddiPayHighHttp5xxRate" in alerts
    assert "DiddiPayProviderTransportErrors" in alerts
    assert "DiddiPayOutboxDeadLetters" in alerts


def test_slo_rules_match_metric_names_and_link_to_runbook() -> None:
    rules = yaml.safe_load(
        (ROOT / "deploy/observability/prometheus/slo-rules.yml").read_text()
    )
    recordings = rules["groups"][0]["rules"]
    alerts = rules["groups"][1]["rules"]
    expressions = " ".join(str(rule["expr"]) for rule in recordings)
    runbook = (ROOT / "OBSERVABILITY_RUNBOOK.md").read_text()

    assert len(recordings) == 7
    assert "diddipay_webhook_events_total{outcome=\"failed\"}" in expressions
    assert "diddipay_outbox_deliveries_total{outcome=~\"retried|unavailable\"}" in expressions
    assert "diddipay_provider_webhooks_total" not in expressions
    assert "diddipay_module_callbacks_total" not in expressions
    assert {rule["alert"] for rule in alerts} == {
        "DiddiPayAvailabilityBudgetFastBurn",
        "DiddiPayAvailabilityBudgetSlowBurn",
        "DiddiPayApiLatencySloBreached",
        "DiddiPayWebhookFailureSloBreached",
        "DiddiPayModuleCallbackFailureSloBreached",
    }
    for rule in alerts:
        anchor = rule["annotations"]["runbook_url"].split("#", 1)[1]
        assert f"## {anchor.replace('-', ' ')}" in runbook.lower()
    assert "Ne jamais recreer une intention avec une nouvelle cle d'idempotence" in runbook


def test_monitoring_images_are_pinned_and_ports_are_local_only() -> None:
    compose = (ROOT / "docker-compose.observability.yml").read_text()

    assert "prom/prometheus:v3.12.0" in compose
    assert "grafana/grafana:13.2.1" in compose
    assert "prom/alertmanager:v0.34.0" in compose
    assert "otel/opentelemetry-collector-contrib:0.160.0" in compose
    assert "grafana/tempo:3.0.3" in compose
    assert '127.0.0.1:${PROMETHEUS_PORT:-49090}:9090' in compose
    assert '127.0.0.1:${GRAFANA_PORT:-43000}:3000' in compose
    assert '127.0.0.1:${ALERTMANAGER_PORT:-49093}:9093' in compose
    assert '127.0.0.1:${TEMPO_PORT:-43200}:3200' in compose
    assert "GRAFANA_ADMIN_PASSWORD must be set" in compose
    assert "/etc/prometheus/slo-rules.yml:ro" in compose


def test_alertmanager_routes_severity_and_reads_webhook_from_secret() -> None:
    config = (
        ROOT / "deploy/observability/alertmanager/alertmanager.yml"
    ).read_text()

    assert "url_file: /run/secrets/alert_webhook_url" in config
    assert "receiver: ops-critical" in config
    assert "receiver: ops-warning" in config
    assert 'severity="critical"' in config
    assert "repeat_interval: 30m" in config
    assert "inhibit_rules:" in config


def test_synthetic_alert_script_never_touches_payment_routes() -> None:
    script = (ROOT / "scripts/test_alertmanager.sh").read_text()

    assert "/api/v2/alerts" in script
    assert "DiddiPaySyntheticTest" in script
    assert "/payment-intents" not in script
