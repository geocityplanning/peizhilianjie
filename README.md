# Hermes REAL Executor

This branch contains the dedicated FastAPI execution service for the Hermes and browser workstation.

## Start

```powershell
uv sync
uv run python -m app.http_executor.init_db
uv run uvicorn app.http_executor.main:app --host 127.0.0.1 --port 8001
```

The service exposes only the four Hermes POST endpoints under `/v1/exec`. It requires the bearer token and `X-Contract-Version: http-executor.v1` header described in the contract.

## Documents

- `contracts/HERMES_REAL_DEPLOYMENT.md`: deployment steps for the dedicated workstation.
- `contracts/hermes-http-v1.md`: request and response contract, field mapping, status rules, and retry rules.
- `contracts/CODEX_HERMES_HTTP_HANDOFF.md`: concise handoff for Hermes integration.

## Scope

The branch keeps the REAL HTTP executor, browser automation actions, configuration templates, and tests. The legacy 8000 frontend, MCP server, old orchestration entrypoint, job manager, and their documentation are excluded. They remain available on `archive/hermes-real-pre-prune`.
