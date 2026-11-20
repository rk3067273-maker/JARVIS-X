# JARVIS X — Phase 2D পরিকল্পনা ও 2D.1 Core

তারিখ: 2026-11-20

## বর্তমান বাস্তব অবস্থা

এই workspace এখনো একটি Python backend/CLI project। এখানে Android Gradle project,
native Settings screen, Android Keystore adapter, launcher activity বা build করা APK নেই।
তাই Phase 2D.1-কে Android app বলে দাবি করা যাবে না।

Read-only environment audit-এ নিচের command/tool পাওয়া যায়নি:

- Java/JDK
- Gradle
- Kotlin compiler
- Android SDK manager
- ADB

কোনো dependency install করা হয়নি।

## যেসব existing safety component reuse করা হয়েছে

- `MainAgentOrchestrator` একমাত্র task entry point।
- `ProviderManager` provider execution/fallback-এর একমাত্র boundary।
- AgentRouter exact model `gpt-6.1-sol`।
- AgentRouter শুধু existing prepaid balance, available balance এবং disabled
  auto-recharge অবস্থায় inference করতে পারে।
- Paid/unknown billing fail-closed।
- Gemini fallback exact-model Free Tier verification ছাড়া চলে না।
- Gemini Free Tier quota শেষ হলে `STOP_AND_WAIT`।
- Timeout `OUTCOME_UNKNOWN`; automatic retry/fallback নেই।
- In-memory duplicate request ID দ্বিতীয়বার পাঠানো হয় না।
- Provider/model/token/request-ID metadata আছে; estimated cost field নেই।

## Phase 2D.1-এ যোগ করা core

`src/jarvis_x/provider_setup/` package এখন দেয়:

1. **Credential abstraction**
   - independent AgentRouter/Gemini save, update এবং delete;
   - masked metadata ছাড়া secret ফেরত দেওয়ার কোনো UI-facing method নেই;
   - mutable key input save-এর পরে best-effort overwrite করা হয়;
   - `InMemoryCredentialStore` শুধু unit test/development adapter—এটি Android
     production storage নয়।
2. **Verification/discovery state**
   - invalid key, permission denied, quota exhausted, unsupported endpoint,
     network failure, invalid response এবং success আলাদা status;
   - verification operation ও charge-risk disclosure;
   - verification-এ inference/prompt পাঠানো হয় না।
3. **Gemini metadata control-plane**
   - official `GET /v1beta/models`;
   - `pageSize`/`pageToken` pagination;
   - API key URL-এ নয়, header-এ;
   - model ID, provider methods, input/output token limits এবং thinking flag;
   - discovery কখনো Free Tier বা paid eligibility অনুমান করে না।
4. **AgentRouter fail-closed control-plane**
   - documented non-inference verification/discovery endpoint না পাওয়া পর্যন্ত
     `UNSUPPORTED_ENDPOINT`;
   - legacy support flag `true` configuration rejected এবং provider adapter-ও
     কোনো `/models` transport request পাঠায় না;
   - manual model field রাখা যায়, কিন্তু সেটি `availability_verified=false` এবং
     router নিজে enable করে না;
   - exact model policy এখনও `gpt-6.1-sol`।
5. **Model Router policy**
   - automatic/manual provider/manual model selection;
   - preferred এবং task-specific preference;
   - coding/reasoning/vision/audio/tool task profile;
   - known capability, context window, reasoning এবং speed metadata ব্যবহার;
   - AgentRouter prepaid safety ও exact verified-free Gemini ছাড়া selection নয়;
   - নিরাপদ model না থাকলে `STOP_AND_WAIT`;
   - manual selection billing policy bypass করতে পারে না।

Model Router এখন pure policy core। এটি production orchestrator dispatch-এ এখনো wire
করা হয়নি; existing fixed `ProviderManager` chain তাই অপরিবর্তিত ও নিরাপদ আছে।

## Recovery verification

Interrupted run-এর পরে একই workspace থেকে 2D.1 recovery verification সম্পন্ন হয়েছে:

- `python -m compileall -q src tests`: PASS
- complete `unittest` discovery: 97/97 PASS
- একই 97 tests Python socket audit hook দিয়ে সব network operation hard-block করে:
  97/97 PASS
- CLI `--health`: healthy (provider keys environment থেকে unset করে চালানো)
- CLI `--orchestrator-health`: healthy, `provider_api_calls=0`
- app-facing AgentRouter control-plane-এ supported endpoint inject করাও rejected
- Gemini discovery metadata billing `UNKNOWN`; router শুধু explicit exact-model
  Free Tier evidence match করলে Gemini candidate নেয়
- prepaid balance, disabled auto-recharge, paid/unknown block, `STOP_AND_WAIT` এবং
  timeout `OUTCOME_UNKNOWN` regression tests PASS

Workspace `.env`/`.env.swp` content recovery-তে খোলা বা parse করা হয়নি; শুধু file
metadata দেখে before/after modification time অপরিবর্তিত নিশ্চিত করা হয়েছে। Test suite
নিজের temporary directories-এ synthetic fake `.env` fixture তৈরি ও পড়ে—সেগুলো
workspace credential নয়।

Recovery verification নিজে কোনো outbound network চালায়নি। Initial 2D.1 documentation
audit-এ official Google/Android/AgentRouter documentation pages web থেকে fetch করা
হয়েছিল; সেটি provider API/inference নয়। তাই পুরো conversation-এর জন্য “zero network
operation” দাবি করা হচ্ছে না। Live provider request, inference বা billing operation
কোনোটিই হয়নি।

## Official provider API audit

### Google Gemini

Official references:

- <https://ai.google.dev/api/models>
- <https://ai.google.dev/api/rest/v1beta/models/list>
- <https://ai.google.dev/gemini-api/docs/pricing>

Verified documentation facts:

- `GET /v1beta/models` available models ও model metadata দেয়।
- Pagination fields `pageSize`, `pageToken`, response `nextPageToken`।
- Metadata-তে model name, base model, version, input/output token limits,
  supported generation methods এবং optional thinking support থাকতে পারে।
- Models endpoint account billing mode, current quota বা exact Free Tier eligibility
  প্রকাশ করে না। Model দেখা যাওয়া মানে free নয়।
- Pricing/eligibility model ও tier অনুযায়ী বদলাতে পারে। তাই hardcoded free-model list
  APK/backend-এ রাখা হবে না। Exact-model Free Tier evidence আলাদাভাবে explicit
  না হওয়া পর্যন্ত model billing `UNKNOWN`।

Verification-এর জন্য Models metadata request prompt বা inference পাঠায় না। UI-তে
এটি `NO_INFERENCE_EXPECTED` হিসেবে দেখাতে হবে; prepaid balance, quota বা billing
dashboard independently verified হয়েছে—এমন দাবি করা যাবে না।

### AgentRouter

Official references checked:

- <https://agentrouter.org/docs/>
- <https://agentrouter.org/docs/opencode.html>
- <https://agentrouter.org/docs/codex.html>

Documentation model availabilityকে dynamic বলে এবং web console/pricing page দেখতে
বলে। Public docs-এ supported non-inference key-verification বা model-list endpoint
নথিভুক্ত পাওয়া যায়নি। 2026-11-20-এর fetched documentation-এ project-mandated
`gpt-6.1-sol`-ও প্রকাশিত তালিকায় নেই।

ফলে:

- project policy নিজে থেকে বদলানো হয়নি;
- `/models` আছে বলে অনুমান করা হয়নি;
- verification নামে paid/test inference পাঠানো হবে না;
- Android UI নিরাপদভাবে `unsupported` দেখাবে এবং manual exact model configuration
  দেবে, কিন্তু unverified model automatic router-এ eligible হবে না।

## Android implementation architecture

### Build requirements

Phone-compatible minimal native build-এর জন্য প্রয়োজন:

1. ARM64-compatible JDK 17 (নির্বাচিত Android Gradle Plugin-এর requirement পুনরায়
   যাচাই করে pin করতে হবে)।
2. Android SDK command-line tools।
3. একটি installed compile SDK/platform এবং matching build-tools।
4. Project-owned Gradle wrapper, pinned Android Gradle Plugin এবং Kotlin plugin।
5. Android 12/API 31 device compatibility; proposed `minSdk` 26। Final
   `compileSdk`/`targetSdk` toolchain setup-এর সময় installed current stable SDK অনুযায়ী
   pin হবে—এখন অনুমান করা হয়নি।

Phone resources বাঁচাতে প্রথম UI increment XML/native Views দিয়ে করা যেতে পারে;
Compose আবশ্যক নয়। Build tools install করা এই phase-এর scope নয়।

### Credential storage

Android adapter design:

1. `AndroidKeyStore`-এ non-exportable AES key generate হবে।
2. Provider API key random IV সহ `AES/GCM/NoPadding` দিয়ে encrypt হবে।
3. Provider ID ও schema version authenticated associated data হবে।
4. Ciphertext app-private `noBackupFilesDir`-এ থাকবে; plaintext preference/config/file-এ
   থাকবে না।
5. `EncryptedSharedPreferences` ব্যবহার হবে না—Android reference-এ এটি deprecated।
6. Manifest-এ backup disabled এবং Android 11-/12+ backup rules-এ credential artifacts
   explicit exclude—দুই স্তরের protection থাকবে। Android 12-এর কিছু OEM device-to-device
   behavior-এর জন্য extraction rulesও আবশ্যক।
7. UI input password-masked; show/hide কেবল current unsaved field-এ। Saved key কখনো
   পুনরায় plaintext দেখানো হবে না।
8. `CharArray`/`ByteArray` যত দ্রুত সম্ভব overwrite; logs, exceptions, analytics,
   clipboard বা saved-state-এ secret যাবে না।

Official Android references:

- <https://developer.android.com/privacy-and-security/keystore>
- <https://developer.android.com/identity/data/autobackup>
- <https://developer.android.com/reference/androidx/security/crypto/EncryptedSharedPreferences>

### App/backend boundary

Preferred boundary একই app process-এর private interface:

```text
Native Provider Settings UI
  -> KeystoreCredentialStore
  -> private ProviderSettingsService
  -> Model Router
  -> MainAgentOrchestrator
  -> ProviderManager
  -> provider adapter
```

- কোনো exported Activity/Service/ContentProvider secret operation expose করবে না।
- Backend localhost HTTP port বা world-readable socket খুলবে না।
- যদি পরে embedded Python রাখা হয়, bridge app-private ও non-exported হতে হবে এবং
  decrypted key শুধু এক request-এর জন্য process memory-তে থাকবে।
- Specialist agent সরাসরি credential store বা provider adapter call করতে পারবে না।

## `.env` migration

- Existing CLI compatibility অক্ষুণ্ণ: development CLI `.env` ব্যবহার করতে পারবে।
- Android install/build কোনো API key চাইবে না এবং `.env` package করবে না।
- Android credential store প্রথম install-এ empty থাকবে; user Settings screen-এ key
  লিখবে। OpenCode/chat/terminal দিয়ে key নেওয়া হবে না।
- Existing workspace `.env` Android app স্বয়ংক্রিয় import করবে না—এতে accidental
  secret exposure ও backup risk বাড়ে।
- App settings path ready হলে `Settings`/`ProviderManager`-এর জন্য আলাদা in-process
  configuration adapter হবে; CLI loader delete বা break করা হবে না।

## পরবর্তী increment

Phase 2D.2 শুরু করার আগে Android toolchain উপস্থিত ও offline/pinned build strategy
অনুমোদিত হতে হবে। এরপর:

1. minimal Android application + launcher icon;
2. non-exported Provider Settings screen;
3. Android Keystore credential adapter ও backup rules;
4. masked save/update/delete UI;
5. Gemini metadata verify/refresh integration;
6. AgentRouter unsupported/manual-state UI;
7. instrumented Keystore, process-death, backup-exclusion এবং no-secret tests;
8. debug APK build, install এবং Android 12 device verification।

Floating overlay, voice এবং notification settings পরের পৃথক Android increments;
2D.1-এ এগুলো implement হয়েছে বলে দাবি করা হচ্ছে না।
