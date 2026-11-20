# Phase 2C.2 — Main Agent Orchestrator

## Boundary

`MainAgentOrchestrator` শুধু `ProviderManager.execute()` ব্যবহার করে। AgentRouter/Gemini নির্বাচন, prepaid policy, Free Tier policy এবং fallback ProviderManager-এর দায়িত্ব।

## IDs

- `task_id`: orchestrator lifecycle identity
- `request_id`: ProviderManager-এর one-send identity

দুটি ID আলাদা এবং prompt/log output-এ প্রকাশিত হয় না। একই active task-এর concurrent execution blocked; একই request ID ProviderManager-ও process-localভাবে দ্বিতীয়বার পাঠায় না।

## Lifecycle

```text
PENDING
VALIDATING
RUNNING
COMPLETED | STOP_AND_WAIT | BLOCKED | FAILED | OUTCOME_UNKNOWN | CANCELLED
```

Invalid transitions `InvalidTaskTransition` দিয়ে rejected হয়। Terminal state থেকে নতুন transition অনুমোদিত নয়।

## Cancellation

- Provider dispatch-এর আগে cancellation → `CANCELLED`, zero provider call।
- Dispatch commit-এর পরে cancellation advisory; provider result বা timeout authoritative।
- Timeout → `OUTCOME_UNKNOWN`, কখনো `CANCELLED`/`FAILED` নয়।
- কোনো retry বা second fallback orchestrator চালায় না।

## Events

Lifecycle events prompt, response content, API key, authorization header বা `.env` value বহন করে না। Event fields সীমাবদ্ধ:

- task ID
- event type/state
- provider/model
- classified reason
- timestamp/event ID

## Persistence

Task/result/duplicate protection process-local। Phase 2C.2 কোনো SQLite, spending ledger বা persistent task database তৈরি করে না।
