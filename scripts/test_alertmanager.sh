#!/usr/bin/env sh
set -eu

alertmanager_url="${ALERTMANAGER_URL:-http://127.0.0.1:49093}"
starts_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

curl --fail --silent --show-error \
  -H "Content-Type: application/json" \
  -X POST "${alertmanager_url}/api/v2/alerts" \
  --data "[
    {
      \"labels\": {
        \"alertname\": \"DiddiPaySyntheticTest\",
        \"severity\": \"warning\",
        \"service\": \"diddipay\"
      },
      \"annotations\": {
        \"summary\": \"DiddiPay synthetic notification test\",
        \"description\": \"No payment is affected. This alert validates the ops notification path.\"
      },
      \"startsAt\": \"${starts_at}\",
      \"generatorURL\": \"manual://scripts/test_alertmanager.sh\"
    }
  ]"

printf '%s\n' "Synthetic warning submitted to ${alertmanager_url}."
