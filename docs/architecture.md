# JARVIS X — Architecture

## Phase 1 foundation

Phase 1 ইচ্ছাকৃতভাবে ছোট এবং standard-library-only। বর্তমান dependency flow:

```text
CLI (__main__)
 ├── Configuration (config)
 ├── Logging and redaction (logging_setup)
 └── Health checks (core.health)
      └── Safe domain errors (core.errors)
```

### Module responsibilities

- `config.py`: environment variables পড়ে immutable settings তৈরি করে; secrets representation থেকে লুকিয়ে রাখে।
- `logging_setup.py`: console logger এবং final rendered-message redaction দেয়।
- `core/errors.py`: user-safe domain exceptions ও stable error codes দেয়।
- `core/health.py`: Python, virtual environment, platform, workspace, configuration এবং storage পরীক্ষা করে।
- `__main__.py`: CLI boundary, exit codes এবং safe error reporting পরিচালনা করে।

## নিরাপত্তা নীতি

1. Secrets source code বা `.env.example`-এ রাখা হবে না।
2. API keys settings-এর `repr`, health payload বা managed logs-এ প্রকাশ করা হবে না।
3. Unexpected exceptions থেকে raw message CLI-তে দেখানো হবে না।
4. Runtime directories configuration পড়ার সময় স্বয়ংক্রিয়ভাবে তৈরি করা হবে না।
5. External actions future permission manager-এর মধ্য দিয়ে যাবে।

## Phase 2A provider foundation

```text
ProviderManager
 ├── AgentRouterProvider (primary)
 │    └── OpenAI-compatible /chat/completions
 └── GeminiProvider (free backup)
      └── Gemini :generateContent

Both adapters
 ├── AIProvider interface
 ├── injected HttpTransport
 ├── normalized ProviderResponse
 ├── ProviderStatus
 ├── optional model discovery
 └── secret-free ProviderError
```

### Failover policy

AgentRouter থেকে fallback শুধু confirmed `quota_exhausted`, `rate_limited` বা `unavailable` reason-এ হয়। Authentication, invalid configuration, invalid response, paid-policy block বা unknown failure সরাসরি থামে। Timeout-কে outcome-unknown ধরা হয়; duplicate charge এড়াতে timeout-এর পরে retry/fallback হয় না।

Google Gemini backup-ও quota/unavailable হলে `ProviderChainError` দিয়ে task থামে। Phase 2A কোনো retry loop রাখে না। Provider switch log-এ শুধু provider name ও classified reason থাকে।

### Billing policy

`BillingTier` হলো `free`, `paid`, অথবা `unknown`। Default `JARVIS_ALLOW_PAID_PROVIDERS=false`; এই অবস্থায় manager শুধু confirmed-free provider call করে। Unknown tier-ও fail-closed। কোনো adapter billing activate বা upgrade করতে পারে না।

### Model and usage data

- Configured model ID environment থেকে আসে।
- Discovery result সরাসরি API response থেকে `ModelInfo` হয়।
- Successful response থেকে provider name, model ID এবং available token counts `UsageRecord`-এ রাখা হয়।
- Pricing catalogue না থাকায় কোনো estimated-cost field রাখা বা fabricated cost দেখানো হয় না।

## ভবিষ্যৎ extension boundaries

পরবর্তী phase-গুলোতে আলাদা interfaces/modules যোগ করা হবে:

- Dynamic model router
- Main orchestrator ও specialized agents
- Memory এবং task management
- Voice engine
- Dynamic Island interface
- Permission-based Android integration
- Security hardening ও recovery

এই future components-এর কোনোটি Phase 2A-তে implement নয়। Provider adapters তৈরি হলেও automated tests injected mock transport ব্যবহার করে; কোনো real provider request করা হয় না।

## Phase 2B billing-safe live boundary

Live request-এর আগে adapter নিজেই billing safety enforce করে; manager bypass করলেও unsafe request পাঠানো যায় না।

### AgentRouter

Required state:

```text
billing_mode = prepaid
prepaid_balance_status = available
auto_recharge_status = disabled
```

`paid`, `unknown`, exhausted balance, enabled auto recharge বা dashboard status unknown হলে transport call-এর আগেই request বন্ধ হয়। Runtime API response-এ credit exhaustion পাওয়া গেলে `balance_exhausted` classify হয় এবং confirmed-free Gemini fallback বিবেচিত হয়। Code dashboard setting পরিবর্তন বা auto recharge বন্ধ করতে পারে না।

### Gemini

Free Tier verification boolean একা যথেষ্ট নয়; `GEMINI_FREE_TIER_VERIFIED_MODEL` অবশ্যই configured `GEMINI_MODEL`-এর exact match হতে হবে। Model পরিবর্তন করলে verification স্বয়ংক্রিয়ভাবে invalid হয়।

### Live-test command

`jarvis_x.providers.live_test`:

- owner-only workspace `.env` parse করে;
- shell expansion করে না;
- model discovery চালায় না;
- সর্বোচ্চ 32 output tokens পাঠায়;
- response content print করে না;
- automatic retry/fallback করে না;
- timeout-কে outcome-unknown দেখায়।

Dashboard এবং Free Tier confirmation command-line flags ব্যবহারকারীর fresh physical verification নির্দেশ করে; code এগুলো নিজে যাচাই করেছে বলে দাবি করে না।

## Phase 2C.1 provider hardening

Provider boundary এখন ছয়টি normalized outcome প্রকাশ করে:

```text
SUCCESS
FALLBACK_ALLOWED
STOP_AND_WAIT
BLOCKED
FAILED
OUTCOME_UNKNOWN
```

- AgentRouter primary model `gpt-6.1-sol`; অন্য configured বা returned model fail-closed।
- Existing prepaid balance exhausted/unavailable হলে verified-free Gemini fallback অনুমোদিত।
- Gemini Free quota exhaustion `STOP_AND_WAIT`; paid fallback নেই।
- Timeout `OUTCOME_UNKNOWN`; retry/fallback নেই।
- `ChatRequest.request_id` in-memory manager lock দিয়ে একবার claim হয়; duplicate/concurrent second send blocked।
- Usage metadata provider name, exact model ID, input/output/total tokens এবং optional provider request ID রাখে।
- অনুমানভিত্তিক cost field সরানো হয়েছে; daily spending guard বা SQLite spending ledger নেই।

## Phase 2C.2 Main Agent Orchestrator

```text
AgentTask
  → validation
  → cancellation gate
  → ProviderManager.execute()
  → normalized ProviderOutcome
  → TaskState + safe lifecycle events + TaskResult
```

Orchestrator provider adapter সরাসরি call করে না এবং নিজে fallback/retry চালায় না। Phase 2C.1 ProviderManager billing safety ও fallback-এর একমাত্র authoritative boundary। Task ID এবং provider request ID আলাদা; concurrent active duplicate task block হয়।

Task lifecycle:

```text
PENDING → VALIDATING → RUNNING
                    ├→ COMPLETED
                    ├→ STOP_AND_WAIT
                    ├→ BLOCKED
                    ├→ FAILED
                    └→ OUTCOME_UNKNOWN

PENDING/VALIDATING/RUNNING-before-dispatch → CANCELLED
```

In-flight cancellation কোনো provider retry বা replacement call সৃষ্টি করে না; ProviderManager outcome-ই final state নির্ধারণ করে।

## Phase 2C.3 Task Execution CLI

CLI-এর task path:

```text
argparse
  → TaskExecutionInterface
  → MainAgentOrchestrator
  → ProviderManager.execute()
  → redacted JSON TaskResult
```

`--dry-run-task` এবং `--orchestrator-health` `LocalDryRunProviderManager` ব্যবহার করে; এটি কোনো real provider adapter/transport তৈরি করে না। `--task` production ProviderManager ব্যবহার করে এবং Phase 2C.1 billing policy bypass করতে পারে না। কোনো FastAPI/HTTP server বা external framework যোগ করা হয়নি।

## Phase 2C.4 Secure Preflight

```text
workspace/.env (exact 0600, non-symlink)
   + recognized process environment overrides
   → safe configuration checks
   → local ProviderManager.statuses()
   → local Orchestrator health
   → PASS / FAIL / NOT_CONFIGURED / NEEDS_MANUAL_VERIFICATION
```

Preflight `urlopen`, provider generation বা model discovery call করে না। API key values report-এ রাখা হয় না। Balance availability, auto-recharge state এবং Gemini Free Tier exact-model state explicit manual attestations—live provider facts হিসেবে দাবি করা হয় না।

## Phase 2C.5 Central Runtime Configuration

```text
workspace .env (regular file, exact 0600)
  → recognized process-environment overrides
  → Settings validation
  → mandatory task-runtime policy validation
  → ProviderManager construction
  → MainAgentOrchestrator
```

`--preflight` এবং actual `--task` একই loader/precedence ব্যবহার করে। API keys blank, AgentRouter model/policy mismatch, auto-recharge unknown, paid provider enablement, Gemini unverified/mismatched model বা model-discovery enablement—সব ProviderManager factory call-এর আগেই fail-closed। Dry-run path centralized runtime loader ও real ProviderManager কোনোটিই ব্যবহার করে না।

## Phase 2D.1 Provider Setup ও Model Router Core

```text
ProviderSettingsService
  ├── CredentialStore abstraction (Android adapter pending)
  ├── non-inference ProviderControlPlane
  │    ├── Gemini paginated Models metadata
  │    └── AgentRouter unsupported/fail-closed
  ├── secret-free ModelCache
  └── safe ProviderSettingsSnapshot

ModelRouter
  └── verified availability + capability + context + billing safety
        ├── AgentRouter exact prepaid gpt-6.1-sol
        ├── exact verified-free Gemini
        └── otherwise STOP_AND_WAIT/BLOCKED
```

Gemini model-list metadata model availability প্রমাণ করে, Free Tier প্রমাণ করে না;
তাই discovered billing tier default `UNKNOWN`। Manual selection safety policy bypass
করতে পারে না। Model Router pure policy হিসেবে unit-tested, কিন্তু existing production
orchestrator dispatch-এ এখনো wire করা হয়নি। Android project/APK/Keystore adapterও এই
increment-এ নেই। বিস্তারিত [`phase-2d.md`](phase-2d.md)।
