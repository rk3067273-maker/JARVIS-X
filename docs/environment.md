# JARVIS X — Environment Baseline

Phase 1 এই Android OpenCode environment-এ যাচাই করা হয়েছে। এটি Termux নয়; Android kernel-এর ওপর Ubuntu userspace চলছে।

## যাচাইকৃত তথ্য

| Component | Value |
|---|---|
| Workspace | `/root/chat/2026-11-19/session-9f3d976f-51d8-48df-9d05-0a01f0270e2e` |
| Operating system | Ubuntu 24.04.3 LTS |
| Architecture | `aarch64` / ARM64 / 64-bit |
| Device | Redmi Note 9 Pro Max |
| Android | Android 12 / API 31 |
| Python | CPython 3.12.3 |
| Node.js | 22.23.2 |
| npm | 10.9.8 |
| Package manager | APT/dpkg উপস্থিত; Phase 1-এ ব্যবহৃত নয় |
| Free storage at audit | প্রায় 11 GB |

## Python virtual environment

Ubuntu image-এ `venv` module আছে, কিন্তু `ensurepip` এবং `python3.12-venv` package নেই। তাই সাধারণ `python3 -m venv .venv` ব্যর্থ হয়। Project-local environment তৈরি করা হয়েছে:

```sh
python3 -m venv --without-pip .venv
```

Pinned bootstrap tools local `site-packages`-এ রাখা হয়েছে:

- pip 24.0
- setuptools 68.1.2
- wheel 0.42.0
- packaging 24.0

কোনো system package পরিবর্তন করা হয়নি।

Phase 2A-ও Python standard library ব্যবহার করে; নতুন runtime package install করা হয়নি। HTTP adapters `urllib`-ভিত্তিক এবং unit tests-এ injected fake transport ব্যবহার করে।

Phase 2B-তেও dependency অপরিবর্তিত। Workspace-local `.env` loader file permission `600` দাবি করে এবং symlink প্রত্যাখ্যান করে।

## Android/qroot compatibility note

এই environment-এ virtual-environment Python symlink সরাসরি চলে, কিন্তু `test -x`, `stat` এবং pip-এর `--python` discovery কখনো false-negative দেয়। তাই validation সরাসরি executable চালিয়ে করা হয়:

```sh
.venv/bin/python --version
```

## বর্তমান Android capability boundary

OpenCode-এর local device capability bridge health check সফল হয়েছে। তবে Accessibility tap/swipe, text input, app control বা screenshot analysis Phase 1-এ implement বা test করা হয়নি। এগুলো কাজ করে বলে দাবি করা হচ্ছে না।
