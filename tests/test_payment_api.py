import uuid

from payfund_app.main import app
from payfund_app.modules.payments.application.ports import (
    InitializePaymentRequest,
    PaymentDirection,
    ProcessorCapabilities,
    ProviderResult,
)
from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.domain import AttemptStatus
from payfund_app.modules.payments.presentation.deps import get_processor_registry


BASE = "/payfund/v1/payment-intents"
HEADERS = {
    "X-Client-ID": "diddigo",
    "X-Service-Key": "test-service-key",
    "Idempotency-Key": "ride-42-payment",
}


def payload(**overrides):
    value = {
        "business_reference": "ride:42",
        "amount": 5_000,
        "currency": "XOF",
        "payer_user_id": str(uuid.uuid4()),
        "payee_user_id": str(uuid.uuid4()),
        "channel": "mobile_money",
        "network": "orange",
        "customer_phone": "+2250700000000",
        "description": "DiddiGo ride 42",
        "metadata": {"ride_id": "42"},
    }
    value.update(overrides)
    return value


def test_create_payment_intent_returns_provider_neutral_action(client):
    response = client.post(BASE, headers=HEADERS, json=payload())
    assert response.status_code == 201
    body = response.json()
    assert body["client_id"] == "diddigo"
    assert body["processor"] == "sandbox"
    assert body["business_reference"] == "ride:42"
    assert body["status"] == "requires_action"
    assert body["next_action"]["type"] == "redirect"
    assert body["attempts"][0]["status"] == "requires_action"
    assert body["attempts"][0]["processor"] == "sandbox"
    assert body["attempts"][0]["next_action"]["type"] == "redirect"
    assert "provider_reference" not in body["attempts"][0]


def test_create_payment_intent_exposes_provider_failure_details(client):
    class FailingProcessor:
        name = "test-failing-provider"
        capabilities = ProcessorCapabilities(
            currencies=frozenset({"XOF"}),
            directions=frozenset({PaymentDirection.COLLECTION}),
            channels=frozenset({"mobile_money"}),
            networks=frozenset({"orange"}),
        )

        def initialize_payment(self, request: InitializePaymentRequest) -> ProviderResult:
            return ProviderResult(
                provider_reference="provider-rejected-reference",
                status=AttemptStatus.FAILED,
                provider_status="invalid_params",
                failure_code="PROVIDER_REQUEST_REJECTED",
                failure_message="provider rejected the checkout parameters",
            )

    registry = ProcessorRegistry()
    registry.register(FailingProcessor())
    app.dependency_overrides[get_processor_registry] = lambda: registry
    try:
        response = client.post(
            BASE,
            headers={**HEADERS, "Idempotency-Key": "provider-failure-details"},
            # The default PAYMENT_PROCESSOR_MODE (sandbox) is not in this registry; name the
            # fake explicitly, as a module sending `processor` would.
            json=payload(processor="test-failing-provider"),
        )
    finally:
        app.dependency_overrides.pop(get_processor_registry, None)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert body["processor"] == "test-failing-provider"
    assert body["provider_status"] == "invalid_params"
    assert body["failure_code"] == "PROVIDER_REQUEST_REJECTED"
    assert body["failure_message"] == "provider rejected the checkout parameters"
    assert body["attempts"][0]["provider_status"] == "invalid_params"
    assert body["attempts"][0]["processor"] == "test-failing-provider"
    assert body["attempts"][0]["failure_code"] == "PROVIDER_REQUEST_REJECTED"
    assert body["attempts"][0]["failure_message"] == (
        "provider rejected the checkout parameters"
    )


def test_create_payment_intent_is_idempotent(client):
    request_body = payload()
    first = client.post(BASE, headers=HEADERS, json=request_body)
    second = client.post(BASE, headers=HEADERS, json=request_body)
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert len(second.json()["attempts"]) == 1


def test_idempotency_key_cannot_be_reused_with_different_amount(client):
    assert client.post(BASE, headers=HEADERS, json=payload()).status_code == 201
    response = client.post(BASE, headers=HEADERS, json=payload(amount=6_000))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_payment_api_requires_service_credentials(client):
    response = client.post(BASE, headers={"Idempotency-Key": "x"}, json=payload())
    assert response.status_code == 401


def test_create_payment_intent_rejects_overlong_idempotency_key(client):
    """Regression: a too-long Idempotency-Key reached the DB unvalidated and crashed with an
    unhandled 500 (psycopg.errors.StringDataRightTruncation) instead of a clean 422 -- found
    via a real DiddiSend request in staging."""
    response = client.post(
        BASE,
        headers={**HEADERS, "Idempotency-Key": "x" * 129},
        json=payload(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_TOO_LONG"


def test_payment_is_visible_only_to_owning_module(client):
    created = client.post(BASE, headers=HEADERS, json=payload()).json()
    response = client.get(
        f"{BASE}/{created['id']}",
        headers={"X-Client-ID": "diddifund", "X-Service-Key": "fund-service-key"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PAYMENT_INTENT_NOT_FOUND"


def test_list_payment_intents_is_scoped_to_client(client):
    client.post(BASE, headers=HEADERS, json=payload())
    response = client.get(
        BASE,
        headers={"X-Client-ID": "diddigo", "X-Service-Key": "test-service-key"},
    )
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1


def test_initialized_payment_cannot_be_cancelled_only_locally(client):
    created = client.post(BASE, headers=HEADERS, json=payload()).json()
    response = client.post(
        f"{BASE}/{created['id']}/cancel",
        headers={"X-Client-ID": "diddigo", "X-Service-Key": "test-service-key"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PAYMENT_OPERATION_CONFLICT"


def test_openapi_documents_all_payment_intent_requests(client):
    schema = client.get("/payfund/v1/openapi.json").json()
    paths = schema["paths"]
    assert BASE in paths
    assert f"{BASE}/{{intent_id}}" in paths
    assert f"{BASE}/{{intent_id}}/cancel" in paths
    assert f"{BASE}/{{intent_id}}/refunds" in paths
    assert f"{BASE}/{{intent_id}}/financial-summary" in paths
    parameters = paths[BASE]["post"]["parameters"]
    names = {parameter["name"] for parameter in parameters}
    assert {"X-Client-ID", "X-Service-Key", "Idempotency-Key"} <= names


def test_openapi_documents_paystack_webhook_request(client):
    schema = client.get("/payfund/v1/openapi.json").json()
    operation = schema["paths"]["/payfund/v1/payments/webhooks/paystack"]["post"]

    assert operation["requestBody"]["required"] is True
    assert "application/json" in operation["requestBody"]["content"]
    parameter_names = {parameter["name"] for parameter in operation["parameters"]}
    assert "X-Paystack-Signature" in parameter_names
