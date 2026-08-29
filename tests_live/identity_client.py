"""Client DiddiFreeID pour la suite live : inscrit/connecte des utilisateurs de test réels
contre le service d'identité, sans jamais toucher à `payfund_app` (suite boîte noire).

Flux (DiddiFreeID_Contrat_API.md §1) : `POST /auth/register` -> `POST /auth/otp/request`
(canal `email`, pour ne jamais déclencher de SMS réel sur un numéro de test) -> saisie manuelle
du code -> `POST /auth/otp/verify`. Les tokens obtenus sont mis en cache sur disque (identifiant
+ refresh_token) pour qu'une seule saisie d'OTP suffise pour tous les runs suivants, tant que le
refresh token reste valide : chaque nouveau run tente d'abord `POST /auth/refresh`.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

IDENTITY_BASE_URL = os.environ.get(
    "IDENTITY_BASE_URL", "https://auth-staging.diddifree.com/identity/v1"
).rstrip("/")

_DEFAULT_CACHE_FILE = Path(__file__).parent / ".auth_cache.json"
CACHE_FILE = Path(os.environ.get("LIVE_TOKEN_CACHE_FILE", _DEFAULT_CACHE_FILE))


@dataclass(frozen=True)
class LiveUser:
    label: str
    user_id: str
    email: str
    phone: str
    access_token: str
    refresh_token: str


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    return json.loads(CACHE_FILE.read_text(encoding="utf-8"))


def _save_cache(data: dict) -> None:
    CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _random_phone() -> str:
    # Format de l'exemple du contrat (+225 + 10 chiffres). Purement synthétique : l'OTP part
    # toujours par e-mail, ce numéro ne reçoit donc jamais de SMS.
    return "+225" + "07" + str(uuid.uuid4().int)[:8]


def _register(client: httpx.Client, label: str) -> tuple[str, str, str]:
    full_name = f"Payfund Live {label}"
    for _ in range(3):
        suffix = uuid.uuid4().hex[:10]
        email = f"payfund.e2e.{label}.{suffix}@example.com"
        phone = _random_phone()
        resp = client.post(
            "/auth/register",
            json={"phone": phone, "email": email, "full_name": full_name},
        )
        if resp.status_code == 201:
            return email, phone, full_name
        if resp.status_code == 409:
            continue
        resp.raise_for_status()
    raise RuntimeError(f"Impossible d'inscrire un utilisateur de test pour {label!r} (409 répété).")


def _try_refresh(client: httpx.Client, entry: dict) -> dict | None:
    resp = client.post("/auth/refresh", json={"refresh_token": entry["refresh_token"]})
    if resp.status_code != 200:
        return None
    data = resp.json()
    entry = {**entry, "access_token": data["access_token"], "refresh_token": data["refresh_token"]}
    return entry


def _register_and_verify(client: httpx.Client, label: str) -> dict:
    email, phone, _full_name = _register(client, label)

    otp_resp = client.post("/auth/otp/request", json={"email": email, "channel": "email"})
    otp_resp.raise_for_status()
    info = otp_resp.json()

    print(
        f"\n[tests_live] OTP requested for '{label}' ({email}) — "
        f"channel={info.get('channel')!r}, expires_in={info.get('expires_in_seconds')}s."
    )
    code = input(f"[tests_live] Enter the OTP code for {email}: ").strip()

    verify_resp = client.post("/auth/otp/verify", json={"email": email, "code": code})
    verify_resp.raise_for_status()
    data = verify_resp.json()

    return {
        "user_id": data["user"]["id"],
        "email": email,
        "phone": phone,
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
    }


def get_or_create_user(label: str) -> LiveUser:
    """Returns a real, verified DiddiFreeID user for the given test-user label.

    Env override: `LIVE_{LABEL}_ACCESS_TOKEN` / `LIVE_{LABEL}_REFRESH_TOKEN` /
    `LIVE_{LABEL}_EMAIL` / `LIVE_{LABEL}_PHONE` skip both the cache and the interactive flow
    entirely — for CI runs against pre-minted tokens.
    """
    env_prefix = f"LIVE_{label.upper()}_"
    env_access = os.environ.get(env_prefix + "ACCESS_TOKEN")
    if env_access:
        return LiveUser(
            label=label,
            user_id=os.environ.get(env_prefix + "USER_ID", ""),
            email=os.environ.get(env_prefix + "EMAIL", ""),
            phone=os.environ.get(env_prefix + "PHONE", ""),
            access_token=env_access,
            refresh_token=os.environ.get(env_prefix + "REFRESH_TOKEN", ""),
        )

    cache = _load_cache()
    entry = cache.get(label)

    with httpx.Client(base_url=IDENTITY_BASE_URL, timeout=20.0) as client:
        if entry:
            refreshed = _try_refresh(client, entry)
            if refreshed is not None:
                cache[label] = refreshed
                _save_cache(cache)
                return LiveUser(label=label, **refreshed)
            # Refresh token revoked/expired (DiddiFreeID §1 : rotation, ou premier run après
            # une révocation) — retombe sur une inscription/OTP neuve.

        entry = _register_and_verify(client, label)
        cache[label] = entry
        _save_cache(cache)
        return LiveUser(label=label, **entry)
