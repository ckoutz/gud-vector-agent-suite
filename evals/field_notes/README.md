# Field-note extraction evaluation suite

Standalone, provider-neutral evaluation tooling for choosing the model behind field-note
extraction. It is **not** product code: nothing here imports `gvas`, and no product module
imports anything here. Only `transports/` speaks HTTP, and it is constructed only by an
explicit `--live` run; loading a manifest or scoring a record can never reach an endpoint,
and no credential value lives in this directory.

Published provider benchmarks do not measure the behavior this product depends on, so a
candidate is judged here instead — over synthetic, redacted, GVAS-shaped field-note turns.

**This suite deliberately selects no model and no provider.** It produces comparable
scorecards; the decision is a separate, human step taken after real candidate runs.

## Layout

| Path | Purpose |
| --- | --- |
| `schema.py` | Typed contract a candidate must return per turn (fields, checklist, status, follow-up, contradiction). |
| `cases.py` | Case fixture models plus semantic validation of the YAML corpus. |
| `cases/dev/`, `cases/holdout/` | The corpus. `dev` is for prompt development; `holdout` is reserved for final selection. |
| `gold.py` | Turns a case's expectations into the result a perfect candidate would emit. |
| `prompt.py` | Evaluation-only system prompt and the JSON Schema derived from `TurnResult`. |
| `adapters/` | The runner contract, deterministic local fakes, and the schema-output adapter shell. |
| `runner.py` | Replays each case turn by turn, threading the candidate's own state forward. |
| `scoring.py` | Metrics, per-category breakdowns, and per-turn violations. |
| `transports/` | Live endpoint clients, injected explicitly for real runs. |
| `manifests/` | Run configurations. Secret-free by construction. |
| `tests/` | Fixture-integrity and scoring tests, run by the normal `pytest` invocation. |

## Running the fake smoke evaluation

No network access, no credentials, no API spend:

```bash
PYTHONPATH=evals uv run -q python -m field_notes.cli
```

That runs `manifests/fake_smoke.yaml`: an oracle candidate that pins every metric at its
ceiling (proving the harness is wired correctly) plus degraded candidates that each break
one behavior, proving every metric actually penalizes what it claims to measure.

Useful flags:

```bash
# score the held-out split too, and show more failing turns
PYTHONPATH=evals uv run -q python -m field_notes.cli --split dev --split holdout --max-violations 20

# machine-readable scorecards for diffing candidates
PYTHONPATH=evals uv run -q python -m field_notes.cli --json-out /tmp/scorecards.json
```

Keep `--split holdout` out of prompt-development loops. Iterating against the holdout split
is what makes a final comparison meaningless.

## What is measured

Extraction quality: field precision/recall (scalars, findings, samples, photos),
unsupported-fact rate, and inference compliance (inferred values must come from the case's
permissible set).

`unsupported_fact_rate` is **fact-level**: the denominator is every fact the candidate
stated on a scored turn — one unit per populated scalar, per finding, per sample, per photo
reference — and the numerator is those the case does not support. A stated fact is supported
when the case expects that value, lists it as a permissible inference, or tolerates the
field; a declared forbidden value or a value filling a declared critical gap is never
supported. A fact that breaks several rules at once still counts once, so the metric stays a
share of facts. `unsupported_fact_turn_rate` reports the separate, coarser question of how
many turns carry at least one unsupported fact.

An invented value is deliberately penalized twice over, by field precision and by this
metric: fabrication is worse than omission, because a reviewer cannot see it.

Conversation behavior: critical-gap detection, false follow-up rate (optional fields must
never trigger a question), one-question target accuracy (exactly one question, on the right
topic), prior-state preservation, contradiction detection, evidence preservation,
premature-finalization resistance, and overall decision accuracy.

Reliability: schema-valid rate.

Operational slots — `latency_p50_ms`, `latency_p95_ms`, `cost_usd_per_case` — are reported as
`n/a` here on purpose. Local fakes have no meaningful latency or cost; only real candidate
runs may populate them.

## Cases

Each case is multi-turn and encodes, per turn: the expected field state, checklist items with
their evidence, permissible inferences, forbidden facts, expected critical gaps, whether a
follow-up is needed and its single target, the expected status, and any contradiction the
candidate should surface. Categories cover clean/complete notes, absent optional fields,
missing critical fields, multiple findings, corrections, contradictions, transcription errors
and garbled proper nouns, follow-up replies, and premature-finalization language.

All content is synthetic. Addresses, names, job numbers, and sample IDs are invented; no real
customer or otherwise sensitive data belongs in this corpus.

Fixtures are validated on load, so a contradictory case (a gap also asserted as filled, an
unresolved gap marked ready for review, a question expected where nothing blocks review)
fails loudly rather than silently mis-scoring a candidate.

## Running the live comparison

`manifests/openrouter.yaml` names the managed and open-weight candidates behind one
OpenRouter key. It is inert until `--live` injects a transport:

```bash
OPENROUTER_API_KEY=<key> PYTHONPATH=evals uv run python -m field_notes.cli \
  --manifest evals/field_notes/manifests/openrouter.yaml \
  --live openai-compatible --json-out /tmp/scorecards.json
```

That spends money: one call per case turn per candidate. Keep `--split holdout` for the
final comparison only, after prompt work on `dev` is finished. The key is read from the
environment variable the manifest names; it is never written to the repo or the scorecards.

## Adding a real candidate later

`adapters/schema_output.py` covers both intended targets — managed structured-output APIs and
an OpenAI-compatible endpoint served by vLLM — because both accept the same JSON Schema and
return the same typed contract.

Transport is injected. The module contains no HTTP client, no provider SDK, and no credential
handling, and running a turn without an injected transport raises `AdapterUnavailableError`,
so a manifest alone can never reach a paid endpoint. Endpoint and model configuration lives
outside the repo: a manifest names *environment variables* (`base_url_env`, `api_key_env`),
never values. See `manifests/candidates.example.yaml`.

To run real candidates, supply a transport implementing `SchemaCompletionTransport` and pass
it in. It returns a `TransportResponse`: the raw JSON plus the `cost_usd` and provider-neutral
`TokenUsage` its provider reported, which is how the operational cost slot gets populated. The
suite stores what the transport reports and never estimates cost from token counts, so price
tables stay outside this repo.

`RecordedTransport` replays captured responses keyed by `case_id:turn_index` — including their
cost and usage — so a real run can be re-scored offline after the fact; it also accepts bare
payload strings when only the JSON was captured.
