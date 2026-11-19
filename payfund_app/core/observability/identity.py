"""One process identity shared by all signals; never derived from business IDs."""

import os
import uuid

from payfund_app.core.config import get_settings

_instance = f"{os.getpid()}-{uuid.uuid4().hex}"


def resource_attributes() -> dict[str, str]:
    settings = get_settings()
    return {
        "service.namespace": "diddi",
        "service.name": settings.observability_service_name,
        "service.version": settings.observability_release_sha,
        "service.instance.id": _instance,
        "deployment.environment.name": "development"
        if settings.observability_environment == "local"
        else settings.observability_environment,
        "diddi.deployment.id": settings.observability_deployment_id,
        "diddi.component": settings.observability_component,
        "diddi.observability.version": "2.0.0",
    }
