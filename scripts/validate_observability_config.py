"""Offline configuration checks with synthetic values; never deploys anything."""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml


def main():
    root = Path(__file__).resolve().parents[1]
    synthetic = {
        "OBSERVABILITY_RELEASE_SHA": "1" * 40,
        "OBSERVABILITY_ENVIRONMENT": "test",
        "OBSERVABILITY_DEPLOYMENT_ID": "synthetic-validation",
        "METRICS_TOKEN": "synthetic-only-" * 4,
        "METRICS_ALLOWED_CIDRS": "10.23.0.0/24",
        "TRACING_OTLP_TRACES_ENDPOINT": "https://collector.invalid/v1/traces",
        "OTEL_EXPORTER_OTLP_HEADERS": "authorization=Bearer%20synthetic",
        "DIDDIOBSERVE_NETWORK": "synthetic-network",
        "API_SCRAPE_TARGET": "api.invalid:443",
        "WORKER_SCRAPE_TARGET": "worker.invalid:443",
        "METRICS_CREDENTIALS_FILE": "/run/secrets/synthetic",
        "SCRAPE_CA_FILE": "/run/secrets/synthetic-ca.pem",
        "OPERATIONS_OWNER": "synthetic-team",
        "RUNBOOK_URL": "https://runbook.invalid/observability",
        "READINESS_PROBE_URL": "https://api.invalid/payfund/v1/ready",
        "BLACKBOX_TARGET": "blackbox.invalid:9115",
    }
    checked = []
    for path in (root / "deploy/diddiobserve").glob("*.yml.template"):
        rendered = re.sub(
            r"\$\{([A-Z_]+)\}", lambda match: synthetic[match[1]], path.read_text()
        )
        assert yaml.safe_load(rendered)
        checked.append(path.name)
    manifest = json.loads((root / "deploy/diddiobserve/manifest.v1.json").read_text())
    assert (
        manifest["state"] == "inventoried"
        and not manifest["central_ingestion_verified"]
    )
    with tempfile.TemporaryDirectory() as temporary:
        env_file = Path(temporary) / "synthetic.env"
        env_file.write_text(
            "\n".join(f"{key}={value}" for key, value in synthetic.items())
        )
        # Strip inherited deployment secrets; Compose output stays in memory, never a proof artifact.
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "USERPROFILE",
                "HOME",
                "TEMP",
                "TMP",
                "APPDATA",
                "LOCALAPPDATA",
                "PROGRAMDATA",
                "PROGRAMFILES",
                "PROGRAMFILES(X86)",
                "PROGRAMW6432",
                "ALLUSERSPROFILE",
                "COMSPEC",
                "HOMEDRIVE",
                "HOMEPATH",
                "DOCKER_CONFIG",
            }
        }
        completed = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(root / "docker-compose.yml"),
                "-f",
                str(root / "docker-compose.diddiobserve.yml"),
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=True,
            env=environment,
        )
        compose = json.loads(completed.stdout)
        assert not compose["services"]["app"].get("ports")
        assert not compose["services"]["payment-worker"].get("ports")
        for name in ("app", "payment-worker"):
            assert (
                compose["services"][name]["environment"]["METRICS_ALLOWED_CIDRS"]
                == synthetic["METRICS_ALLOWED_CIDRS"]
            )
            assert compose["services"][name]["logging"]["options"] == {
                "max-size": "10m",
                "max-file": "5",
            }
        assert (
            compose["services"]["payment-worker"]["environment"][
                "OBSERVABILITY_COMPONENT"
            ]
            == "worker"
        )
    result = {
        "templates_valid_yaml": sorted(checked),
        "compose_parsed": True,
        "public_api_worker_ports_removed": True,
        "docker_rotation": "10m x 5",
        "central_connection_attempted": False,
        "deployment_performed": False,
    }
    output = root / "docs/observability/evidence/configuration.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
