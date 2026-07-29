#!/usr/bin/env bash
#
# Example curl calls against every MT5 AI Bridge endpoint.
# Illustrative only -- not part of the application or its test suite.
#
# Requires the server running locally against a running, logged-in MT5
# terminal (`make run`), listening on the default host/port below.
#
# Usage: bash examples/curl_examples.sh

set -euo pipefail

BASE_URL="${MT5_AI_BRIDGE_URL:-http://127.0.0.1:8000}"

echo "== GET / (liveness) =="
curl -s "$BASE_URL/" | python -m json.tool

echo
echo "== GET /health =="
curl -s "$BASE_URL/health" | python -m json.tool

echo
echo "== GET /account =="
curl -s "$BASE_URL/account" | python -m json.tool

echo
echo "== GET /positions =="
curl -s "$BASE_URL/positions" | python -m json.tool

echo
echo "== GET /candles/{symbol}/{timeframe} =="
curl -s "$BASE_URL/candles/EURUSD/H4?count=250" | python -m json.tool

echo
echo "== GET /strategy/trend/{symbol}/{timeframe} =="
curl -s "$BASE_URL/strategy/trend/EURUSD/H4?count=250" | python -m json.tool

echo
echo "== GET /strategy/multi-timeframe/{symbol} =="
curl -s "$BASE_URL/strategy/multi-timeframe/EURUSD?count=250" | python -m json.tool

echo
echo "== GET /risk/trade-levels =="
curl -s "$BASE_URL/risk/trade-levels?signal=buy&entry_price=1.0850&atr=0.0025" \
  | python -m json.tool

echo
echo "== GET /analysis/market-structure/{symbol}/{timeframe} (DEPRECATED, still functional) =="
curl -s "$BASE_URL/analysis/market-structure/EURUSD/H4?count=200" | python -m json.tool

echo
echo "== POST /api/v2/analyze (canonical SMC pipeline) =="
curl -s -X POST "$BASE_URL/api/v2/analyze" \
  -H "Content-Type: application/json" \
  -d @"$(dirname "$0")/analyze_request_example.json" \
  | python -m json.tool
