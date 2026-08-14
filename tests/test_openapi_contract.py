"""Regression tests for the executable HTTP contract exposed by FastAPI."""

from payfund_app.main import app


def test_every_json_success_response_has_an_openapi_schema():
    schema = app.openapi()
    missing: list[str] = []

    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for status, response in operation.get("responses", {}).items():
                if not status.startswith("2") or status == "204":
                    continue
                body_schema = (
                    response.get("content", {})
                    .get("application/json", {})
                    .get("schema")
                )
                if not body_schema:
                    missing.append(f"{method.upper()} {path} -> {status}")

    assert missing == []


def test_wallet_openapi_exposes_security_and_frontend_fields():
    schema = app.openapi()
    components = schema["components"]

    assert components["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    pin_set = components["schemas"]["PinSetResponse"]["properties"]
    assert "recovery_codes" in pin_set

    transfer = components["schemas"]["TransferRequest"]["properties"]
    assert "step_up_token" in transfer
    assert "otp_code" not in transfer
    assert "/payfund/v1/wallet/transfer/step-up/request" not in schema["paths"]

    transaction = components["schemas"]["TransactionItem"]["properties"]
    assert "direction" in transaction

    webhook = schema["paths"]["/payfund/v1/wallet/webhooks/paystack"]["post"]
    assert "requestBody" in webhook
