# JARVIS X

JARVIS X একটি modular AI assistant project। Phase 2C-এর provider/orchestrator safety-এর ওপর **Phase 2D.1**-এ user-controlled provider setup contract, non-inference verification/model discovery core এবং billing-safe Model Router যোগ হয়েছে।

> Provider code শুধু স্পষ্টভাবে call করলে network ব্যবহার করতে পারে। Health check ও unit tests কোনো real API request চালায় না। Native Android UI/APK, Keystore adapter, voice, overlay এবং Android Accessibility এখনো তৈরি হয়নি। Phase 2D audit/plan: [`docs/phase-2d.md`](docs/phase-2d.md)।

## প্রয়োজনীয় environment

- Python 3.12+
- Linux/Android-compatible shell
- Project-local `.venv`
- Runtime dependency: নেই (Python standard library only)

বর্তমান Android OpenCode environment-এর বিস্তারিত [`docs/environment.md`](docs/environment.md)-এ আছে।

## Setup

Project root থেকে চালান:

```sh
./scripts/bootstrap_venv.sh
```

Scriptটি:

1. `.venv` না থাকলে `--without-pip` দিয়ে তৈরি করে;
2. local bootstrap tools-এর pinned versions যাচাই করে;
3. প্রয়োজন হলে শুধু local `.venv`-এ pinned bootstrap tools বসায়;
4. `src/` directory-কে local environment-এ register করে।

এটি APT বা global Python পরিবর্তন করে না।

## Configuration

বর্তমান development CLI configuration environment variables থেকে পড়ে। Live API test শুধু workspace-local `.env` সরাসরি parse করে; API keys `os.environ`-এ export করে না। Final Android app-এর জন্য `.env` নয়, app Settings + Android Keystore path পরিকল্পিত; 2D.1-এ তার platform-neutral contract আছে, native adapter এখনো নেই।

```sh
cp .env.example .env
chmod 600 .env
```

এরপর local editor দিয়ে `.env` পূরণ করুন। API key `cat`, `echo`, shell history বা terminal output-এ দেখাবেন না।

ফোনে নিরাপদভাবে key/model বসানোর ধাপ: [`docs/local-configuration.md`](docs/local-configuration.md)। API key chat বা OpenCode output-এ দেবেন না।

গুরুত্বপূর্ণ variables:

| Variable | Default | উদ্দেশ্য |
|---|---|---|
| `JARVIS_ENV` | `development` | Runtime environment name |
| `JARVIS_LOG_LEVEL` | `INFO` | Console logging level |
| `JARVIS_DATA_DIR` | `data` | ভবিষ্যৎ local data directory |
| `JARVIS_LOG_DIR` | `logs` | ভবিষ্যৎ log directory |
| `JARVIS_PRIMARY_PROVIDER` | `agentrouter` | Phase 2C.1 fixed primary |
| `JARVIS_ALLOW_PAID_PROVIDERS` | `false` | Paid/unknown-cost providers fail-closed |
| `AGENTROUTER_BASE_URL` | `https://agentrouter.org/v1` | OpenAI-compatible API base |
| `AGENTROUTER_API_KEY` | খালি | Primary provider secret |
| `AGENTROUTER_MODEL` | `gpt-6.1-sol` | Enforced primary model ID |
| `AGENTROUTER_BILLING_MODE` | `unknown` | শুধু explicit `prepaid` live request-এর জন্য অনুমোদিত |
| `AGENTROUTER_PREPAID_BALANCE_STATUS` | `unknown` | Dashboard-verified `available`/`exhausted`/`unavailable` |
| `AGENTROUTER_AUTO_RECHARGE_STATUS` | `unknown` | Dashboard-verified `disabled`/`enabled` |
| `AGENTROUTER_MODELS_ENDPOINT_SUPPORTED` | `false` | অবশ্যই `false`; documented metadata endpoint নেই |
| `GEMINI_BASE_URL` | Google `v1beta` URL | Gemini API base |
| `GEMINI_API_KEY` | খালি | Free backup provider secret |
| `GEMINI_MODEL` | খালি | User-selected Gemini model ID |
| `GEMINI_FREE_TIER_VERIFIED` | `false` | Current Free Tier documentation manually checked |
| `GEMINI_FREE_TIER_VERIFIED_MODEL` | খালি | Verification-এর exact model ID |

`.env` Git-এ commit করবেন না। Secret values settings representation, health output বা managed log output-এ প্রকাশ করা হয় না।

## Provider policy

Phase 2C.1 chain:

```text
AgentRouter primary
    └── confirmed quota/credit exhaustion, rate limit, or unavailability
          └── Google Gemini free fallback
```

- Authentication/configuration errors fallback দিয়ে লুকানো হয় না।
- Timeout-এর ফল অনিশ্চিত হতে পারে, তাই timeout-এর পরে retry বা fallback হয় না।
- `JARVIS_ALLOW_PAID_PROVIDERS=true` Phase 2C.1-এ invalid configuration।
- AgentRouter কেবল `prepaid + balance available + auto recharge disabled` হলে চলতে পারে। `paid` ও `unknown` blocked।
- Gemini শুধু model-specific Free Tier confirmation match করলে চলতে পারে।
- কোনো adapter billing activate, plan upgrade বা paid provider register করে না।
- AgentRouter model policy `gpt-6.1-sol`; Gemini model environment থেকে আসে এবং exact Free Tier verification-এর সঙ্গে match করতে হয়।
- AgentRouter-এর undocumented `/models` endpoint call করা হয় না; support flag `true` configuration rejected।
- Normalized outcomes: `SUCCESS`, `FALLBACK_ALLOWED`, `STOP_AND_WAIT`, `BLOCKED`, `FAILED`, `OUTCOME_UNKNOWN`।
- একই in-memory request ID দ্বিতীয়বার বা concurrentভাবে পাঠানো blocked।
- কোনো daily spending cap বা spending ledger নেই।

Phase 2D.1 setup core-এ Gemini official Models metadata endpoint paginatedভাবে parse করা যায়। Discovery result কখনো নিজে থেকে Free Tier ধরে না; exact-model Free Tier verification আলাদা safety evidence। AgentRouter-এর documented non-inference discovery endpoint না থাকায় app-facing control-plane fail-closed `unsupported` থাকে। এই নতুন core existing CLI task path-এ automatic network call যোগ করে না।

## চালানো

Health check:

```sh
.venv/bin/python -m jarvis_x --health
```

Version:

```sh
.venv/bin/python -m jarvis_x --version
```

Orchestrator health—সম্পূর্ণ local:

```sh
.venv/bin/python -m jarvis_x --orchestrator-health
```

Secure live-readiness preflight—কোনো provider request নয়:

```sh
.venv/bin/python -m jarvis_x --preflight
```

Preflight `.env` symlink/permission, recognized configuration, billing attestations, exact models এবং local ProviderManager/Orchestrator health পরীক্ষা করে। এটি balance, quota, auto-recharge dashboard বা Free Tier-এর current external state নিজে প্রমাণ করে না। বিস্তারিত [`docs/preflight.md`](docs/preflight.md)।

Local dry-run—কোনো provider API call নয়:

```sh
.venv/bin/python -m jarvis_x --dry-run-task "তোমার কাজের বিবরণ"
```

Configured ProviderManager-এর মাধ্যমে task:

```sh
.venv/bin/python -m jarvis_x --task "তোমার কাজের বিবরণ"
```

Phase 2C.5-এ `--task` ও `--preflight` একই centralized secure loader ব্যবহার করে: workspace `.env` base, তারপর recognized process-environment overrides। Unsafe/missing `.env`, blank mandatory key, billing-policy mismatch বা unverified Gemini model ProviderManager তৈরির আগেই task block করে। `--dry-run-task` ও `--orchestrator-health` configuration secret বা `.env` পড়ে না।

CLI JSON result-এ task ID, state, timestamps, provider/model, token usage, optional provider request ID এবং safe error summary থাকে। History শুধু current process-এর মধ্যে থাকে। বিস্তারিত [`docs/cli.md`](docs/cli.md)।

## Live connection test

নিরাপত্তা ও exact commands: [`docs/live-testing.md`](docs/live-testing.md)। Preflight command network request পাঠায় না:

```sh
.venv/bin/python -m jarvis_x.providers.live_test --provider agentrouter
.venv/bin/python -m jarvis_x.providers.live_test --provider gemini
```

`--execute` না দিলে কোনো request যায় না। Execution সর্বোচ্চ এক request এবং 32 output tokens-এ সীমাবদ্ধ।

## পরীক্ষা

```sh
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m unittest discover -s tests -v
```

## Project layout

```text
src/jarvis_x/          Python package
src/jarvis_x/core/     Core errors and health checks
src/jarvis_x/providers/ Provider adapters, transport, policy, and models
src/jarvis_x/provider_setup/ App settings contract, metadata verification/cache, and Model Router
src/jarvis_x/orchestrator/ Task lifecycle and ProviderManager coordination
tests/                 Standard-library unit tests
scripts/               Android/Linux-compatible setup helpers
docs/                  Environment and architecture notes
```

Phase 2C.5 architecture [`docs/architecture.md`](docs/architecture.md), [`docs/orchestrator.md`](docs/orchestrator.md), [`docs/cli.md`](docs/cli.md) এবং [`docs/preflight.md`](docs/preflight.md)-এ নথিভুক্ত আছে। Phase 2D.1 audit, API limitations, Android security design এবং migration plan [`docs/phase-2d.md`](docs/phase-2d.md)-এ আছে।
