# Codex Monitor 📊

**A floating macOS widget + CLI for OpenAI Codex quota and multi-account management — without opening the Codex app, and without ever touching your tokens unless you ask.**

<p align="center">
  <img src="docs/screenshots/panel.png" alt="Codex Monitor panel — every account with remaining quota, reset time, reset credits, subscription and token expiry" width="380">
  &nbsp;&nbsp;
  <img src="docs/screenshots/strip.png" alt="Collapsed vertical strip" width="46">
</p>

[![macOS 14+](https://img.shields.io/badge/macOS-14%2B-000000?style=flat&logo=apple&logoColor=white)](#3--quick-start) · [![Swift 5 · SwiftUI](https://img.shields.io/badge/Swift-5%20%C2%B7%20SwiftUI-F05138?style=flat&logo=swift&logoColor=white)](Sources/CodexMonitor) · [![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B%20%C2%B7%20stdlib%20only-3776AB?style=flat&logo=python&logoColor=white)](bin/codex-acct) · [![Read-only by design](https://img.shields.io/badge/tokens-read--only%20by%20design-2ea44f?style=flat)](#6--how-it-works) · [![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat)](LICENSE) · [🇨🇳 中文说明](README_CN.md)

🈶 *The widget's UI text is currently Chinese; every string lives in [`Sources/CodexMonitor/Strings*.swift`](Sources/CodexMonitor), so an English localisation is a small PR.*

💡 *Codex Monitor reads the same `auth.json` that Codex CLI / the Codex desktop app use, calls the same read-only usage endpoints, and tails the same session logs — so the numbers match what Codex shows, in real time.*

## Contents

- [1. 🎯 Why](#1--why)
- [2. ✨ Features](#2--features)
- [3. 🚀 Quick Start](#3--quick-start)
- [4. 🧭 Using the widget](#4--using-the-widget)
- [5. 👥 Multiple accounts — `codex-acct`](#5--multiple-accounts--codex-acct)
- [6. ⚙️ How it works](#6--how-it-works)
- [7. 🔐 Security notes](#7--security-notes)
- [8. 🧪 Development & tests](#8--development--tests)
- [9. ❓ FAQ](#9--faq)
- [License](#license)

## 1. 🎯 Why

If you use Codex with a ChatGPT subscription you have probably run into these:

- **You want to see your quota without opening the Codex app.** Opening the app (or a CLI session) can refresh tokens and rewrite `~/.codex/auth.json`, which is annoying when you copy that file to other machines.
- **You have several ChatGPT accounts** and want to use them on *one* machine. `codex logout` revokes tokens, and logging in from a browser that already has a ChatGPT session silently picks the wrong account — so people end up dedicating one machine per account.
- **The usage page lags.** You want the number the Codex app shows, at the moment it changes.

Codex Monitor solves all three: a read-only widget that shows every account's quota, a one-click login flow that adds accounts into isolated directories, and a real-time feed taken from Codex's own session logs.

## 2. ✨ Features

**Widget (menu bar + floating panel)**

- Per account: email, plan (Pro / Plus / Free / Team …), **remaining quota** for every rate-limit window (5-hour, weekly, 30-day …), **reset time + countdown**, **reset credits** ("Full reset" coupons and their expiry), per-model limits (e.g. GPT-5.3-Codex-Spark), **subscription expiry**, access-token validity, last credential refresh.
- **Real-time** — the panel tails Codex session rollouts and updates within a second of each model response; the usage API (30 s by default) is the fallback and calibration.
- **Collapsible**: full panel → the active account as a card and the others as one-line rows (or all expanded) → a 46-px vertical strip pinned to the screen edge.
- Non-activating floating panel (clicking it never steals focus), draggable, remembers position, three window levels (always on top / normal / pinned to the desktop), starts at login via a LaunchAgent.
- One-click **switch** between accounts, **copy / export** any account's `auth.json`, **re-login** an account whose session was revoked.

**Multi-account**

- **Add accounts from the GUI**: an OAuth (authorization code + PKCE) login that opens the authorize page in a *private* browser window, so the account you are already logged into in your browser is never picked by mistake. No ChatGPT setting needs to be enabled.
- Device-code login as a fallback (for remote / headless use).
- Every account lives in its own `~/.codex-accounts/<name>/` directory, which doubles as an isolated `CODEX_HOME`. Switching copies one file; `codex-acct run <name>` runs Codex with a different account in parallel without switching at all.
- Refreshed tokens are synced back: when Codex rewrites `~/.codex/auth.json`, the stored copy of that account is updated, so your archive never holds a stale refresh token.

**CLI (`codex-acct`)** — `add`, `import`, `save`, `list`, `status`, `use`, `run`, `env`, `export`, `sync`, `refresh`, `remove`. Python 3, standard library only.

## 3. 🚀 Quick Start

Requirements: macOS 14+, Xcode Command Line Tools (for `swiftc`), [Codex CLI](https://github.com/openai/codex) logged in at least once (or not — the widget will tell you), Python 3.9+.

```bash
git clone https://github.com/asimfish/codex_monitor.git
cd codex_monitor
./scripts/install.sh
```

`install.sh` compiles `CodexMonitor.app` with the system Swift toolchain (ad-hoc signed, ~1 minute), installs it to `~/Applications`, registers a LaunchAgent so it starts at login, and links the CLI to `~/.local/bin/codex-acct`. Re-run it to upgrade; `./scripts/uninstall.sh` removes everything except your stored accounts.

The panel appears at the top-right of your screen showing whatever `~/.codex/auth.json` is logged in as. To add another account:

1. Click **添加账号 / Add account** (panel footer or menu-bar menu) and give it a directory name.
2. Click **Open in Chrome incognito** (or your default browser), sign in with the *other* ChatGPT account.
3. The browser redirects back to `localhost:1455`; the window turns into **Login succeeded** and the account appears in the panel. Optionally click **Make active**.

## 4. 🧭 Using the widget

| Where | What |
|---|---|
| Menu bar | remaining % of the active account; menu: switch account, show/hide/collapse panel, refresh, window level, launch at login, add account |
| Panel header | last refresh · refresh now · ⋯ settings (refresh interval 15 s – 5 min, window level, launch at login, log) · ˄ collapse to strip · × hide |
| Active card | quota bars per window, reset time + countdown, per-model limits, data-source line (`live · from Codex session event hh:mm:ss` or `API · fetched hh:mm:ss`), reset credits, subscription expiry, token validity, last refresh |
| Other accounts | one-line rows (click a row to expand, ˄ to collapse) or **Expand all / Collapse all**; each has **Switch** and a ⧉ menu: copy `auth.json` contents, copy path, reveal in Finder, export…, re-login… |
| Strip | status dot, vertical quota bar, remaining %, short countdown; only the bottom arrow expands (the rest just drags) |

Colours: green > 50 % remaining, orange 20–50 %, red ≤ 20 % or limit reached; yellow dot = showing cached data; grey = not loaded yet.

## 5. 👥 Multiple accounts — `codex-acct`

Everything the GUI does is also available from the terminal:

```bash
codex-acct save main             # archive the current ~/.codex login as an account (just a copy)
codex-acct add work              # device-code login into ~/.codex-accounts/work (see note below)
codex-acct import old ~/Downloads/auth.json   # bring in an auth.json you already have
codex-acct list                  # table: email / plan / token expiry / subscription / which one is active
codex-acct status                # fetch quota for every account: remaining %, reset time, reset credits
codex-acct use work              # switch: write this account into ~/.codex/auth.json (old one synced/backed up first)
codex-acct run alt1              # don't switch — run codex with alt1 in this terminal (parallel use)
eval "$(codex-acct env alt1)"    # same, for every codex call in this shell
codex-acct export work ~/tmp/    # copy an auth.json out for another machine
codex-acct sync                  # pull a refreshed ~/.codex/auth.json back into its account directory
codex-acct refresh work          # (optional, confirmed) explicit token refresh
```

Layout:

```
~/.codex/auth.json                    what Codex CLI / the desktop app actually read
~/.codex-accounts/
  main/auth.json                      one directory per account = one isolated CODEX_HOME
  work/auth.json
  _backup/auth-<email>-<time>.json    backups of an un-archived main auth.json taken before `use`
  .cache/usage-<account_id>.json      the widget's last usage snapshot per account
```

`codex-acct run` symlinks `config.toml`, `AGENTS.md`, `skills/`, `plugins/`, `agents/` into the account directory, so configuration is shared and only credentials and session history are separate.

> **Device-code login needs a ChatGPT setting.** `codex login --device-auth` only works after the target account enables *Device code authorization for Codex* under ChatGPT → Settings → Security. The GUI's browser login does not need it — that is why it is the default.

## 6. ⚙️ How it works

**Data sources**

| Source | Used for | Frequency |
|---|---|---|
| `auth.json` (JWT claims) | email, plan, account id, subscription expiry, token validity | on change (polled every 10 s) |
| `GET {chatgpt_base_url}/wham/usage` | rate-limit windows, per-model limits, credits, reset-credit count | every 30 s (configurable 15 s – 5 min) |
| `GET …/wham/rate-limit-reset-credits` | reset coupons and their expiry | every 3 min |
| `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (`token_count` events) | **real-time** rate limits after every model response — identical to what the Codex app shows | tailed every 2 s (only bytes appended to files modified in the last 15 min) |

Live events win when they are newer than the last API fetch, or when the API still reports *less* usage for the same window than an event from the last 2 minutes (the usage endpoint can lag the per-response headers slightly). `~/.codex/sessions` is attributed to the active account (events before the last `auth.json` change are ignored); `~/.codex-accounts/<name>/sessions` to that account.

**Read-only guarantees**

The widget never calls the OAuth refresh endpoint and never writes any `auth.json` on its own. The only writes are user-initiated: *Switch*, *Save as account*, *Refresh token…* (confirmation dialog; the button only appears when a token is expired/rejected), *Re-login…*, and the one-way "adopt" copy that mirrors a `~/.codex/auth.json` Codex itself just refreshed into the matching account directory (no network involved).

**Login flow**

Browser mode reproduces Codex CLI's own OAuth client: same `client_id`, `redirect_uri=http://localhost:1455/auth/callback`, `S256` PKCE, form-encoded `POST https://auth.openai.com/oauth/token`. The app runs the loopback listener itself and writes an `auth.json` in exactly the shape Codex writes. Device-code mode spawns `codex login --device-auth` with `CODEX_HOME` pointing at the account directory and parses the URL and code from its output.

## 7. 🔐 Security notes

- `auth.json` files contain bearer tokens. Codex Monitor stores them with mode `0600` and never sends them anywhere except `chatgpt.com` / `auth.openai.com`. "Copy auth.json" puts the whole file (including tokens) on your clipboard — that is the point, but be aware.
- One `auth.json` on two machines: whichever refreshes first may invalidate the other's refresh token. Prefer `codex-acct export` per machine, and avoid refreshing the same account from both sides.
- `codex logout` revokes tokens server-side (`/oauth/revoke`). With per-account directories you never need it.
- Nothing in this repository contains credentials; `~/.codex-accounts` is outside the repo.

## 8. 🧪 Development & tests

```
Sources/CodexMonitor/   Swift (SwiftUI + AppKit): NSPanel widget, MenuBarExtra, OAuth login, rollout tailer
  Strings*.swift        all user-facing text (Chinese UI)
bin/codex-acct          Python CLI (stdlib only)
scripts/build.sh        swiftc build + ad-hoc codesign (outside the source tree, see below)
scripts/install.sh      install + LaunchAgent;  scripts/uninstall.sh
tests/test_cli.py       sandboxed end-to-end test of codex-acct (temp CODEX_HOME, fake JWTs, no network)
tests/run_store_tests.sh  Swift tests: profile store, adopt/switch/backup, JWT/ISO8601 parsing,
                        usage decoding, rollout parsing + tailing, OAuth helpers
```

```bash
./tests/run_store_tests.sh && python3 tests/test_cli.py
CODEX_MONITOR_DEMO=1 CODEX_MONITOR_SNAPSHOT=/tmp/panel.png ~/Applications/CodexMonitor.app/Contents/MacOS/CodexMonitor
#   demo mode: fabricated accounts, no network — renders the panel to a PNG and quits
CODEX_MONITOR_TEST_LOGIN=browser ~/Applications/CodexMonitor.app/Contents/MacOS/CodexMonitor
#   self-test of the OAuth plumbing: listener → fake callback → token exchange rejected (expected)
```

The build happens in `~/Library/Caches/CodexMonitor/build` because iCloud-synced folders attach extended attributes asynchronously and `codesign` then rejects the bundle.

## 9. ❓ FAQ

**The widget shows "Token expired/rejected (401)" although the token's `exp` is in the future.** The session was revoked server-side (password change, "log out of all devices", plan change …). Use *Re-login…* on that account.

**The device-code page says to enable device code authorization.** Enable it under ChatGPT → Settings → Security for that account, or simply use the default browser login.

**Numbers differ from the usage page for a minute.** The usage API can lag; the live line under the bars tells you which source you are looking at. The Codex app and the widget use the same session events.

**Port 1455 is busy.** Another `codex login` (or the Codex app's login) is running; finish or cancel it and retry.

## License

[MIT](LICENSE)
