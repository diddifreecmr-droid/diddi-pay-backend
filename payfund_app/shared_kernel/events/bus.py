"""Bus d'événements interne.

Transport : Redis Pub/Sub au démarrage (DiddiFreeID_Contrat_API.md §4). Le contrat précise que
le transport est « un détail d'infrastructure », d'où le port ci-dessous : migrer vers
RabbitMQ/Kafka ne touchera que l'adaptateur.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

import redis

from payfund_app.core.config import get_settings
from payfund_app.shared_kernel.events.types import Event

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]


class EventBusPort(Protocol):
    def publish(self, event: Event) -> None: ...
    def subscribe(self, event_name: str, handler: Handler) -> None: ...


class InMemoryEventBus:
    """Adaptateur synchrone, utilisé par les tests et le mode mono-processus."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self.published: list[Event] = []

    def publish(self, event: Event) -> None:
        self.published.append(event)
        self._dispatch(event.to_dict())

    def subscribe(self, event_name: str, handler: Handler) -> None:
        self._handlers.setdefault(event_name, []).append(handler)

    def _dispatch(self, message: dict[str, Any]) -> None:
        for handler in self._handlers.get(message.get("event", ""), []):
            try:
                handler(message)
            except Exception:  # un abonné en échec ne doit pas casser les autres
                logger.exception("Handler en échec pour %s", message.get("event"))


class RedisEventBus:
    """Redis Pub/Sub. L'écoute tourne dans un thread démon lancé au démarrage de l'app."""

    def __init__(self, url: str | None = None, channel: str | None = None) -> None:
        settings = get_settings()
        self._client = redis.Redis.from_url(url or settings.redis_url)
        self._channel = channel or settings.event_bus_channel
        self._handlers: dict[str, list[Handler]] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def publish(self, event: Event) -> None:
        self._client.publish(self._channel, json.dumps(event.to_dict()))

    def subscribe(self, event_name: str, handler: Handler) -> None:
        self._handlers.setdefault(event_name, []).append(handler)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _listen(self) -> None:
        pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(self._channel)
        while not self._stop.is_set():
            message = pubsub.get_message(timeout=1.0)
            if not message:
                continue
            try:
                data = json.loads(message["data"])
            except (ValueError, TypeError):
                logger.warning("Message non-JSON ignoré sur %s", self._channel)
                continue
            for handler in self._handlers.get(data.get("event", ""), []):
                try:
                    handler(data)
                except Exception:
                    logger.exception("Handler en échec pour %s", data.get("event"))


_bus: EventBusPort | None = None


def get_bus() -> EventBusPort:
    global _bus
    if _bus is None:
        _bus = RedisEventBus()
    return _bus


def set_bus(bus: EventBusPort) -> None:
    """Point d'injection pour les tests."""
    global _bus
    _bus = bus
