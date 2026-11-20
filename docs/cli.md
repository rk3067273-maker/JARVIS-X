# Phase 2C.3 — Task Execution CLI

## Commands

```sh
.venv/bin/python -m jarvis_x --help
.venv/bin/python -m jarvis_x --health
.venv/bin/python -m jarvis_x --orchestrator-health
.venv/bin/python -m jarvis_x --preflight
.venv/bin/python -m jarvis_x --dry-run-task "description"
.venv/bin/python -m jarvis_x --task "description"
```

Optional task arguments:

```text
--task-type TYPE
--max-output-tokens NUMBER
```

## Configuration boundary

- `--task` centralized runtime loader দিয়ে strict workspace `.env` পড়ে, তারপর recognized process-environment overrides প্রয়োগ করে।
- Mandatory runtime policy validation pass না করলে production `ProviderManager` তৈরি হয় না।
- `--dry-run-task` এবং `--orchestrator-health` Settings, `.env`, AgentRouter বা Gemini transport ব্যবহার করে না।
- `--preflight` owner-only `.env` read করে এবং recognized process-environment keys-কে higher precedence দেয়; কোনো provider request করে না।
- Phase 2C.5 verification-এ actual `--task` চালানো হয় না; live provider request আলাদা explicit authorization চায়।

## Output

Task output JSON fields:

- task ID
- current terminal state
- normalized provider outcome
- created/updated timestamps
- provider and model
- redacted result
- input/output tokens
- provider request ID, when returned
- safe error reason/summary
- process-local history scope

No API key, authorization header, `.env` value বা sensitive prompt event/log-এ রাখা হয় না। Configured secrets result text বা provider request ID-তে accidentalভাবে দেখা গেলেও CLI serializer redact করে।

## Exit codes

| State | Code |
|---|---:|
| `COMPLETED` | 0 |
| `STOP_AND_WAIT` | 10 |
| `BLOCKED` | 11 |
| `FAILED` | 12 |
| `OUTCOME_UNKNOWN` | 13 |
| `CANCELLED` | 14 |

## Dry-run guarantee

`--dry-run-task`:

- real ProviderManager factory call করে না;
- AgentRouter/Gemini adapter বানায় না;
- transport/network call করে না;
- local validation ও lifecycle execution করে।

## Persistence limitation

Task history, cancellation state ও duplicate protection process-local। CLI process restart হলে পুরোনো task history থাকে না। Phase 2C.3 কোনো SQLite বা persistent server যোগ করে না।

## Local API

FastAPI বা অন্য API framework project dependency নয়। Phase 2C.3 কোনো HTTP endpoint/public server expose করে না; local API security আলাদা অনুমোদিত phase-এর বিষয়।
