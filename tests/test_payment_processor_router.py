from dataclasses import dataclass

import pytest

from payfund_app.modules.payments.application.ports import (
    PaymentDirection,
    ProcessorCapabilities,
)
from payfund_app.modules.payments.application.processor_router import (
    ProcessorRegistry,
    ProcessorRoutingError,
)


@dataclass
class FakeProcessor:
    name: str
    capabilities: ProcessorCapabilities

    def initialize_payment(self, request):
        raise NotImplementedError

    def verify_payment(self, provider_reference):
        raise NotImplementedError

    def parse_webhook(self, raw_body, headers):
        raise NotImplementedError

    def refund_payment(self, request):
        raise NotImplementedError


def processor(name, *, directions, channels=frozenset(), networks=frozenset()):
    return FakeProcessor(
        name=name,
        capabilities=ProcessorCapabilities(
            currencies=frozenset({"XOF"}),
            directions=frozenset(directions),
            channels=frozenset(channels),
            networks=frozenset(networks),
        ),
    )


def test_registry_selects_paystack_for_current_collection():
    registry = ProcessorRegistry()
    registry.register(
        processor(
            "paystack",
            directions={PaymentDirection.COLLECTION, PaymentDirection.REFUND},
            channels={"mobile_money", "card"},
        )
    )

    selected = registry.select(
        currency="XOF",
        direction=PaymentDirection.COLLECTION,
        channel="mobile_money",
    )
    assert selected.name == "paystack"


def test_direct_adapter_can_replace_paystack_without_changing_selection_contract():
    registry = ProcessorRegistry()
    registry.register(
        processor(
            "paystack",
            directions={PaymentDirection.COLLECTION},
            channels={"mobile_money"},
        ),
        priority=100,
    )
    registry.register(
        processor(
            "orange_money_direct",
            directions={PaymentDirection.COLLECTION},
            channels={"mobile_money"},
            networks={"orange"},
        ),
        priority=10,
    )

    selected = registry.select(
        currency="XOF",
        direction=PaymentDirection.COLLECTION,
        channel="mobile_money",
        network="orange",
    )
    assert selected.name == "orange_money_direct"


def test_preferred_processor_must_support_requested_capability():
    registry = ProcessorRegistry()
    registry.register(
        processor(
            "paystack",
            directions={PaymentDirection.COLLECTION},
            channels={"card"},
        )
    )

    with pytest.raises(ProcessorRoutingError, match="no processor supports"):
        registry.select(
            currency="XOF",
            direction=PaymentDirection.COLLECTION,
            channel="mobile_money",
            preferred_processor="paystack",
        )


def test_duplicate_processor_registration_is_rejected():
    registry = ProcessorRegistry()
    adapter = processor("paystack", directions={PaymentDirection.COLLECTION})
    registry.register(adapter)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(adapter)
