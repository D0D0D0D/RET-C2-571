# RET-C2-571 — Retail Category Intelligence Orchestrator

> **Category**: Cat 2 (domain workflow)
> **Industry**: RET

## Overview

Retail Category Intelligence Orchestrator puts a category's own sales record next to what the
outside world is saying about it, and writes up where the two agree, where they diverge, and
where either turned. The result is a review document meant to go into a decision meeting, not a
dashboard.

It suits a category manager weighing an assortment or shelf change, an analyst who has a CSV of
a sales series and wants correlation, divergence and inflection points written up, or a planner
checking recent public interest in a product line against how it actually sold.

**Sales or price data is the anchor, and the agent will not proceed without it.** If the series
cannot be acquired, the run ends as `status=error` rather than producing a trend-only report.
That is deliberate: a report built from public chatter alone reads exactly like a report built
from evidence, and the second one is the only one worth putting in front of a decision.

External signals degrade the other way round. Free public sources are the baseline; paid sources
are used when keys are injected and simply recorded as degraded when they are not, so a missing
key narrows the evidence rather than stopping the run. Whatever was missing is stated in the
report rather than quietly omitted.

When the requested scope is ambiguous — no topic, or an explicit request to confirm — the run
suspends and asks a person, rather than guessing a scope and producing a confident answer to a
question nobody asked.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Integration

Provide:

- an AgentCore runtime with a checkpointer — `memory_enabled` and `hitl.enabled` are both true,
  and scope confirmation suspends and resumes across calls;
- caller authentication in front of the entry adapter (see *Input*);
- optionally, a `BaseLLM` through `config["llm"]` and a secret accessor through
  `config["secrets_get"]` for the paid trend sources (`X_API_KEY`, `PERPLEXITY_API_KEY`). Neither
  is wired by the standalone server, so on that path report generation and the paid tier both
  take their degraded paths.

Runtime parameters are read from `config/config.yaml`:

| Key | Default | Meaning |
|---|---|---|
| `max_retry` | `3` | Retry ceiling for the pipeline |
| `timeout_seconds` | `30` | Per-run timeout |
| `memory_enabled` | `true` | Required for the suspend/resume checkpoint |
| `hitl.enabled` / `hitl.max_hitl` | `true` / `4` | Scope-confirmation cycles per session |

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent is designed to run on the platform, which supplies the caller identity, the language-model client and the secret store. Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Supplied by the platform's own package registry at build time. It is not published on public PyPI, so `pip install -e .` alone does not fetch it. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run on AGENTIC STAR, and the agent does not fall back to a degraded
mode when it is absent. The standalone server in `src/api/server.py` does start and serves
`/invoke`, `/resume` and `/health`. What it lacks is the platform's contributions: no
language-model client and no secret accessor are injected, so report generation and the paid
trend tier run degraded, and the suspend/resume checkpoint is held in process memory — which
means a restart loses every suspended thread and a second replica cannot resume the first one's.
Wire a durable checkpointer before treating suspend/resume as reliable. The test suite runs
without a platform connection.

## Input

`POST /invoke` takes a JSON body with `input` and an optional `session_id`. **`input` is itself a
JSON string** carrying the research request:

| Field | Meaning |
|---|---|
| `topic` | The category or product line to research |
| `target` | Whose performance to analyse |
| `period_days` | Window for the analysis |
| `inline_data` | The sales or price series as CSV, inline |
| `data_url` | Alternatively, an HTTP(S) URL the series is fetched from |

Callers must be authenticated. Trust stamped by verified upstream middleware is used as-is;
otherwise, when `INVOKE_AUTH_TOKEN` is set on the server environment, a matching
`Authorization: Bearer` header promotes the caller to `VERIFIED_EXTERNAL`. A caller that is
neither middleware-verified nor token-authenticated is rejected with **401**, and a verified
caller below `VERIFIED_EXTERNAL` is **403** — the nodes that reach external data and the language
model all require verified-external trust.

```bash
curl -X POST http://127.0.0.1:8000/invoke \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${INVOKE_AUTH_TOKEN}" \
  -d '{
    "input": "{\"topic\":\"植物性プロテイン\",\"target\":\"自社\",\"period_days\":30,\"inline_data\":\"date,sales\\n2026-01-01,100\\n2026-01-02,103\\n2026-01-03,106\"}",
    "session_id": "category-review-001"
  }'
```

**`POST /resume`** continues a suspended scope confirmation. It takes `thread_id`, `feedback` and
`session_id`. Ownership is checked fail-closed: an unknown thread is **404**, a thread belonging
to another session or another caller is **403**, and a thread that has already completed is
**409** — so a captured `thread_id` cannot be replayed by someone else or re-run after the fact.

`GET /health` returns a liveness response and takes no authentication.

Input is bounded: `input` over 20,000 characters is rejected, and a `data_url` must be HTTP(S) —
other schemes, internal and metadata addresses are blocked, and the response size is capped.

## Output

The standard AgentCore envelope is preserved. `output` and `result` carry the review document,
`fusion_report` carries the structured analysis, alongside `status` and `error_log`.

`fusion_report` reports `correlations` (including whether a correlation was determinable at all),
`divergences`, `inflections` and `gaps` — the last being what could not be established, stated
rather than dropped.

```json
{
  "output": "稟議書\n対象: 植物性プロテイン\n…",
  "result": "稟議書\n対象: 植物性プロテイン\n…",
  "fusion_report": {
    "correlations": {"determinable": true},
    "divergences": [],
    "inflections": [],
    "gaps": []
  },
  "status": "success",
  "error_log": []
}
```

When the scope needs confirming, the response instead reports a suspended run with a `thread_id`
to resume against.

Values above are illustrative; real output depends on the data supplied.

## Security and Limitations

The nodes that reach outside — data acquisition, trend research, report generation — and the
outer entry and exit nodes each require verified-external trust, so an insufficiently trusted
caller is stopped before any of them runs. Outbound fetches go through a guarded fetcher that
permits only HTTP(S), blocks internal and metadata addresses, enforces a host allowlist and caps
the response size, because a user-supplied URL is a server-side request forgery vector by
default.

The outer exit node is the last layer before delivery: it scans the report for credential
patterns and scrubs personal information that came in with external-source content. Report
generation additionally annotates expressions that carry advertising-representation risk under
Japanese law (景表法), so a phrase that would be a problem in a published claim is flagged rather
than silently passed on.

Limits worth knowing before deploying this template as-is:

- **The checkpoint is in process memory.** Suspended scope confirmations do not survive a restart
  and are not visible to another replica. This is the first thing to change for a real
  deployment.
- **Free-tier signals are public forum activity**, not market share or category demand. They
  measure how much a topic is being talked about in a few public sources, which correlates with
  demand sometimes and with news cycles often. The report cites its sources so a reader can
  judge.
- **Correlation is reported, not interpreted.** `determinable: false` means the data did not
  support a correlation, which is a different statement from "no relationship exists".
- **A missing paid key silently narrows the evidence base.** The run continues and records the
  degraded source; check `sources_used` before treating a quiet report as a complete one.

Output is machine-generated and assistive. It does not substitute for expert review, and any
ordering, pricing or assortment decision should be verified by a person against the primary data.

## Release

- Version: 1.0.0
- Category: fixed multi-step domain workflow (Cat 2) with an inner workflow subgraph
- Generation mode: `llm`, degrading when no client is injected
- HITL: enabled — `hitl.max_hitl: 4`; `memory_enabled: true` (checkpointer required)
- Persistence: in-process checkpoint only
- External API clients: deployment-injected; none bundled

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection, without secrets and without network access.

## Project Structure

```
src/          agent implementation (nodes, graphs, services, schemas)
tests/        unit, integration and boundary tests
config/       agent manifest and runtime configuration
docs/         design and test documentation
```

## Customising

1. Replace the in-process checkpointer with a durable one before relying on suspend/resume.
2. Inject a `BaseLLM` through `config["llm"]` for real report generation, and a secret accessor
   through `config["secrets_get"]` to enable the paid trend sources.
3. Adjust the fetch host allowlist for the data sources your deployment is allowed to reach.
4. Extend the representation-risk expression list for your own compliance policy.
5. Re-run the test suite.

## Documentation

`docs/02_design.md` carries the node flow, the state schema, the fusion analysis and the security
design. `docs/03_test_spec.md` records the test specification and the verification results.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
