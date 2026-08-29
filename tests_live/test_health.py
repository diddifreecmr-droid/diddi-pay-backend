"""Sanity check : le déploiement répond avant d'aller plus loin."""

from __future__ import annotations

import httpx


def test_health_is_up(api: httpx.Client) -> None:
    resp = api.get("/health")
    assert resp.status_code == 200, resp.text
