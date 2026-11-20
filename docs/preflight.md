# Phase 2C.4 — Secure Configuration Preflight

## Command

```sh
.venv/bin/python -m jarvis_x --preflight
```

এই command কোনো provider request, model discovery, paid operation বা external network call করে না।

Preflight ও actual `--task` একই `load_runtime_values()` source ও precedence policy ব্যবহার করে। Actual task অতিরিক্ত mandatory execution validation চালিয়ে তারপরই ProviderManager তৈরি করতে পারে।

## File safety

Workspace `.env`:

- উপস্থিত regular file হতে হবে;
- symlink হতে পারবে না;
- permission exact `0600` হতে হবে;
- `.gitignore`-এ `.env` থাকতে হবে;
- duplicate/invalid syntax rejected হবে।

Existing `.env` preflight পরিবর্তন বা overwrite করে না।

Phone-based local setup guide: [`local-configuration.md`](local-configuration.md)।

## Precedence

```text
built-in defaults
    < workspace .env
    < recognized process-environment keys
```

শুধু JARVIS X-এর recognized configuration keys merge হয়। Unknown process variables report বা Settings-এ copy হয় না। Explicit blank process value একই `.env` key-কে blank হিসেবে override করতে পারে এবং configured status হারাবে।

## Status values

| Status | অর্থ |
|---|---|
| `PASS` | Local requirement satisfied |
| `FAIL` | Unsafe বা policy-violating value |
| `NOT_CONFIGURED` | Required key/model/file অনুপস্থিত বা blank |
| `NEEDS_MANUAL_VERIFICATION` | Dashboard/documentation attestation এখনো প্রয়োজন |

## Attestation boundary

- `AGENTROUTER_PREPAID_BALANCE_STATUS=available` current balance query নয়।
- `AGENTROUTER_AUTO_RECHARGE_STATUS=disabled` dashboard state-এর user attestation।
- Gemini Free Tier verification exact configured model-এর manual attestation।
- Model বদলালে verified-model mismatch হয় এবং preflight fail করে।
- Current balance/quota preflight নিজে জানে না।

## Output safety

Report API key, authorization header, `.env` value বা secret-shaped content দেখায় না। শুধু check name, safe status এবং controlled explanation থাকে।

## Exit behavior

- All checks `PASS`: exit `0`
- কোনো `FAIL`: exit `1`
- `NOT_CONFIGURED` বা `NEEDS_MANUAL_VERIFICATION`: exit `2`
