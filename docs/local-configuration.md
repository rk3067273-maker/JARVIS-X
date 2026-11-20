# Phase 2C.6 — ফোনে নিরাপদ Local Configuration

API key কখনো chat, OpenCode prompt, screenshot, `echo`, `cat`, shell history বা terminal output-এ দেবেন না। Key শুধু ফোনের local `.env` editor-এ paste করুন।

## ১. Workspace-এ যান

```sh
cd "/root/chat/2026-11-19/session-9f3d976f-51d8-48df-9d05-0a01f0270e2e"
```

Existing `.env` থাকলে copy, recreate বা overwrite করবেন না। Permission নিশ্চিত করুন:

```sh
chmod 600 .env
```

## ২. ফোনের local editor খুলুন

```sh
nano .env
```

`nano` না থাকলে:

```sh
vi .env
```

Key বসাতে `echo`, `sed -i` বা command-line argument ব্যবহার করবেন না—এগুলো shell history-তে secret রাখতে পারে।

## ৩. AgentRouter fields

AgentRouter dashboard-এর API key page থেকে key copy করে local editor-এ শুধু এই field-এর `=`-এর পরে paste করুন:

```text
AGENTROUTER_API_KEY=
```

Policy fields:

```text
AGENTROUTER_MODEL=gpt-6.1-sol
AGENTROUTER_BILLING_MODE=prepaid
AGENTROUTER_MODELS_ENDPOINT_SUPPORTED=false
```

Dashboard-এ freshভাবে existing prepaid balance এবং auto recharge যাচাই করার পরে:

```text
AGENTROUTER_PREPAID_BALANCE_STATUS=available
AGENTROUTER_AUTO_RECHARGE_STATUS=disabled
```

এই দুটি value manual attestation; code current balance বা dashboard state নিজে যাচাই করে না। Balance exhausted/unavailable হলে corresponding status ব্যবহার করা যায় এবং verified-free Gemini fallback policy কার্যকর হবে।

## ৪. Gemini fields

Google AI Studio/API key management থেকে key copy করে local editor-এ paste করুন:

```text
GEMINI_API_KEY=
```

Google-এর current official Gemini API documentation/AI Studio model list-এ যে exact model Free Tier-এ আছে সেটি যাচাই করে লিখুন:

```text
GEMINI_MODEL=<exact model ID>
```

একই exact model-এর Free Tier eligibility manualভাবে নিশ্চিত করার পর:

```text
GEMINI_FREE_TIER_VERIFIED=true
GEMINI_FREE_TIER_VERIFIED_MODEL=<exact same GEMINI_MODEL value>
```

Model বদলালে পুরোনো verification invalid। নতুন model যাচাই না করা পর্যন্ত:

```text
GEMINI_FREE_TIER_VERIFIED=false
GEMINI_FREE_TIER_VERIFIED_MODEL=
```

রাখুন।

## ৫. Save করুন

`nano`:

1. `Ctrl+O`
2. `Enter`
3. `Ctrl+X`

`vi`:

1. `i`
2. edit
3. `Esc`
4. `:wq`
5. `Enter`

তারপর permission পুনরায় নিশ্চিত করুন:

```sh
chmod 600 .env
```

## ৬. Safe readiness check

```sh
.venv/bin/python -m jarvis_x --preflight
```

Preflight field values বা secrets দেখায় না। এটি শুধু `PASS`, `FAIL`, `NOT_CONFIGURED` এবং `NEEDS_MANUAL_VERIFICATION` status দেখায়। কোনো provider request বা network call করে না।

## ৭. Configuration precedence

```text
built-in defaults
  < workspace .env
  < recognized process-environment overrides
```

Unknown environment variables ignored। Process environment-এ recognized key blank থাকলেও সেটি `.env` value-কে blank হিসেবে override করবে এবং task fail-closed হবে।
