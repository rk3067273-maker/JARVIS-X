# Phase 2B — Billing-safe live testing

এই commandগুলো API key print করে না এবং `.env` process environment-এ export করে না। Live execution-এর আগে AgentRouter dashboard ও Gemini Free Tier documentation ব্যবহারকারীকে নিজে যাচাই করতে হবে। Code এই external facts নিশ্চিত করতে পারে না।

## Local secret file

```sh
cp .env.example .env
chmod 600 .env
```

Local editor দিয়ে values বসান। `.env` Git-ignored এবং symlink হতে পারবে না।

সম্পূর্ণ phone-based configuration instructions: [`local-configuration.md`](local-configuration.md)।

Live execution-এর আগে network-free secure preflight:

```sh
.venv/bin/python -m jarvis_x --preflight
```

Preflight status শুধু local configuration readiness। `available`, `disabled` এবং Free Tier verification user/dashboard attestations; এগুলো provider-এর current balance, quota বা billing state-এর live proof নয়।

## AgentRouter safety values

Dashboard-এ **auto recharge disabled** এবং positive existing prepaid balance freshভাবে দেখার পর:

```text
AGENTROUTER_BILLING_MODE=prepaid
AGENTROUTER_PREPAID_BALANCE_STATUS=available
AGENTROUTER_AUTO_RECHARGE_STATUS=disabled
```

Primary model fixed:

```text
AGENTROUTER_MODEL=gpt-6.1-sol
```

Code auto recharge disable, credit purchase বা balance top-up করতে পারে না। Status unknown হলে request blocked।

Preflight—কোনো request নয়:

```sh
.venv/bin/python -m jarvis_x.providers.live_test --provider agentrouter
```

Fresh dashboard confirmation-এর পর সর্বোচ্চ একটি request:

```sh
.venv/bin/python -m jarvis_x.providers.live_test \
  --provider agentrouter \
  --execute \
  --confirm-auto-recharge-disabled \
  --confirm-prepaid-balance-available
```

## Gemini Free Tier safety values

Google-এর current documentation-এ configured model Free Tier-এ আছে নিশ্চিত করার পর:

```text
GEMINI_FREE_TIER_VERIFIED=true
GEMINI_FREE_TIER_VERIFIED_MODEL=<exact GEMINI_MODEL value>
```

Preflight—কোনো request নয়:

```sh
.venv/bin/python -m jarvis_x.providers.live_test --provider gemini
```

Fresh Free Tier confirmation-এর পর সর্বোচ্চ একটি request:

```sh
.venv/bin/python -m jarvis_x.providers.live_test \
  --provider gemini \
  --execute \
  --confirm-free-tier
```

## Fixed execution limits

- Maximum output: 32 tokens
- Requests per command: 1
- Automatic retry: disabled
- Automatic provider fallback: disabled in live test
- Model discovery: disabled
- Balance depletion testing: prohibited
- Response text: not printed
- Timeout: outcome unknown; no duplicate request
