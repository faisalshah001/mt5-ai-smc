# Examples

Usage examples for the MT5 AI Bridge API. These assume the server is
already running locally (`make run`) against a running, logged-in MT5
terminal, listening on `http://127.0.0.1:8000`.

For the authoritative request/response reference, see
[`../docs/API.md`](../docs/API.md).

| File | Shows |
|---|---|
| [`curl_examples.sh`](curl_examples.sh) | One `curl` call per endpoint, including the canonical and legacy structure endpoints |
| [`analyze_request_example.json`](analyze_request_example.json) | A sample request body for `POST /api/v2/analyze` |
| [`python_client_example.py`](python_client_example.py) | A minimal, dependency-free Python client that calls `/api/v2/analyze` and prints a short summary |

None of these scripts are part of the application or its test suite —
they are illustrative only and are not covered by the CI pipeline.
