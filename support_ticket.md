# Support ticket — iOS app "Unable to connect to server" (v26.2 build 625)

## Executive summary

The ZenMoney iOS app shows **"Unable to connect to server"** on launch, but the **server is not the problem**. Across four independent log captures from 2026-05-08 (one cold install of the App Store Production build + three warm relaunches of the TestFlight beta, all on the same device), TLS handshakes succeed, certificates verify, and HTTP 200/204 responses come back from your backend. The actual failure is local to the app and reproduces in two coupled events:

1. **`Missing user details, aborting makePollerRequest`** — the app cannot build authenticated sync/poller requests because in-memory user state is empty.
2. **`BUG IN CLIENT OF libsqlite3.dylib: database integrity compromised by API violation: vnode unlinked while in use`** — fired three times in a row against `Library/zenmoney.sqlite`, `…-shm`, `…-wal`. The app deletes its own SQLite files while it still holds open file descriptors on them.

This pattern fires within 35–90 seconds of every cold or warm launch, on both the Production and TestFlight builds of `26.2 (625)`. Reinstalling does **not** fix it (cold install reproduces). The most likely cause is a code path in the iOS app — probably a cache-reset, account-switch, sign-out, or migration handler — that unlinks the SQLite store without first closing the open `sqlite3*` handles.

---

> Device- and account-identifying values (device GUID, Apple DSIDs, container/bundle UUIDs)
> are redacted in this public copy. The unredacted table went to ZenMoney support.

## Environment

| Field | Value |
|---|---|
| App | ZenMoney iOS, bundle id `ru.zenmoney.ZenMoneyNative` |
| Version | `26.2`, build `625` |
| Builds affected | App Store **Production** and **TestFlight** beta (`betaExternalVersionID` redacted) |
| Device GUID | redacted (iPhone, A15 ECID family) |
| Apple account DSID | redacted (two distinct values, one per build) |
| App container UUIDs | redacted (Production and TestFlight have distinct sandbox containers) |
| Bundle UUIDs | redacted (distinct per build) |
| Capture date | 2026-05-08, 14:31–14:48 local (UTC+1) |
| Captures attached | `eror_log.md` (1.9 k lines), `error_log_1.md` (5.7 k), `error_log_2.md` (7.5 k), `error_log_3.md` (535) — Apple Console export, level/time/process/message TSV format |

---

## Reproduction

The bug fires on every launch attempt I've captured. The user-facing symptom is identical in all cases: a screen that says **"Unable to connect to server"**.

| Capture | Scenario | Outcome |
|---|---|---|
| `error_log_1.md` | Cold install of the **Production** App Store build (kernel logs `App Store Fast Path` for every framework, fresh sandbox container, first 200s succeed, then both signature errors fire within 50 s) | Reproduces |
| `eror_log.md` | Warm relaunch of the TestFlight build from Spotlight (cached `SBSceneManager` state) | Reproduces |
| `error_log_2.md` | Warm relaunch via TestFlight handoff (`SBTransitionSwitcherModifierEvent` from `com.apple.TestFlight` → `ru.zenmoney.ZenMoneyNative`) | Reproduces |
| `error_log_3.md` | Warm relaunch from background (focused capture of just the failure window, ~35 s) | Reproduces |

Confirmed not to help: app reinstall, killing the app and relaunching, switching between TestFlight and Production builds. (Network swap was not retested but is ruled out by the evidence below — TLS and HTTP both succeed in every capture.)

---

## What works (network is healthy)

In every capture, before the failure crystalises:

- TCP+TLS 1.3 handshakes complete successfully on multiple connections.
- Certificates verify: `Certificate verification result: OK`, `TLS Trust result 0`.
- The app receives **HTTP 200 / 204 responses** from your backend on multiple tasks (`error_log_1.md` shows 71 successful connection events; `error_log_2.md` shows 71 successes; `error_log_3.md` shows 6 successes including two 200s on connection C37).
- Both HTTP/2 and QUIC are negotiated successfully against different host pools.

The detector script counted across the four files:

```
connection_success: 71 / 71 / 6 / 21
tls_success:       46 / 46 / 3 / 12
http_error (5xx):   0 /  0 / 0 /  0   ← no server-side HTTP errors anywhere
```

Apple's log privacy redacts your hostnames to `Hostname#<hash>:443` tokens, so I cannot give you the literal subdomains. Recurring host hashes that appear in multiple captures (so you can correlate against your edge / load-balancer logs by IP-mapping if useful):

| Hash token | Total occurrences | Behaviour |
|---|---:|---|
| `Hostname#e8a9f070:443`, `#c968761e:443`, `#64453505:443`, `#0f1e00e4:443` | 61 each | High-volume, all 200s |
| `Hostname#2ea4a531:443` | 49 | 200s |
| `Hostname#26b812f4:443` (mapped to `IPv4#47092164:443`) | 37 | C37 in `error_log_3.md`, succeeds |
| `Hostname#00e493d7:443` (mapped to `IPv4#b8c81247:443`) | 23 | **TCP RST post-TLS in both `error_log_2.md` (line ~7445) and `error_log_3.md` (line 244)** |
| `Hostname#6aa6d5c7:443` (mapped to `IPv4#3a557a1a:443`) | 21 | mixed |

Some long-lived QUIC/HTTP2 streams to `Hostname#00e493d7:443` and a couple of TCP connections (e.g. `error_log_1.md` C6/C7) are reset by peer (`flags=[R.]`, `errno 54`, `Lower protocol stack error post TLS handshake`). This is **secondary** — it happens *after* the missing-user-details abort and may simply be your edge cleanly closing connections that never produced an authenticated request. It is **not** the cause of the user-visible error.

---

## What fails (the actual bug, with evidence)

Both signatures fire in **every** capture. Line numbers and timestamps below are from the attached files.

### 1) Missing user details

| File | Line | Timestamp | Process | Message |
|---|---:|---|---|---|
| `error_log_1.md` | 5312 | 14:40:41.224 | `Zenmoney` | `Missing user details, aborting makePollerRequest` |
| `error_log_1.md` | 5421 | 14:41:13.978 | `Zenmoney` | `Missing user details, aborting makePollerRequest` (2nd) |
| `eror_log.md` | 1209 | 14:31:44.912 | `Zenmoney` | `Missing user details, aborting makePollerRequest` |
| `eror_log.md` | 1346 | 14:32:22.548 | `Zenmoney` | `Missing user details, aborting makePollerRequest` (2nd) |
| `error_log_2.md` | 5411 | 14:45:17.231 | `Zenmoney` | `Missing user details, aborting makePollerRequest` |
| `error_log_3.md` | 138 | 14:48:11.227 | `Zenmoney` | `Missing user details, aborting makePollerRequest` |

In `error_log_3.md` this fires only **2.9 seconds** after the process is foregrounded and right after a successful HTTP 200 response on connection C37 — so the network round-trip is fine, the app simply has no user object in memory to attach credentials to.

### 2) SQLite store unlinked while open (always cascades to all three files)

Exact stanza, repeated in every capture:

```
error  Zenmoney  BUG IN CLIENT OF libsqlite3.dylib: database integrity compromised by
                 API violation: vnode unlinked while in use:
                 /private/var/mobile/Containers/Data/Application/<UUID>/Library/zenmoney.sqlite
error  Zenmoney  invalidated open fd: 16 (0x11)
error  Zenmoney  BUG IN CLIENT OF libsqlite3.dylib: ... zenmoney.sqlite-shm
error  Zenmoney  invalidated open fd: 18 (0x11)
error  Zenmoney  BUG IN CLIENT OF libsqlite3.dylib: ... zenmoney.sqlite-wal
error  Zenmoney  invalidated open fd: 17 (0x11)
```

Where it appears:

| File | Lines | Timestamp |
|---|---|---|
| `error_log_1.md` | 5422–5427 (Production cold install) | 14:41:13.979 |
| `eror_log.md` | 1363–1368 | 14:32:22.551 |
| `error_log_2.md` | 7173+ | 14:45:52.262 |
| `error_log_3.md` | 283+ | 14:48:43.644 |

Three details that I think are diagnostic:

- The path is in **`Library/`**, not `Caches/` or `tmp/` — iOS will not automatically purge it under storage pressure. Whatever unlinked these files did so deliberately.
- All three SQLite sidecar files (`-shm`, `-wal`) are unlinked in the same millisecond. This is a single sweep, not three independent races.
- The fds invalidated (16, 17, 18 in `error_log_1.md`) are consecutive and held simultaneously — i.e. the database is in active use when the unlink happens. The classic causes are: an `unlink(2)` / `[NSFileManager removeItem]` on the DB path before `sqlite3_close_v2`, or `[NSURL setResourceValues:]` moving the file out from under it.
- The **`Missing user details`** error fires immediately before each cascade, which suggests a code path that says "I don't have a user → reset/clear local state" and then the reset blows up because a background read is still in flight.

### 3) Sandbox denies (informational, almost certainly not relevant)

`error  kernel  Sandbox: Zenmoney(<pid>) deny(1) sysctl-read kern.bootargs` — appears 4–22 times per capture. This is iOS denying read access to a kernel sysctl that third-party apps don't have entitlement for. Apple's own logs flag it as `error` but it is benign and present in healthy iOS apps. Mentioning only because a quick scan would surface them.

---

## My read on the root cause

I think the user-visible "Unable to connect to server" message is **two layers downstream** of the actual bug. In order:

1. The iOS app takes some action — most likely a cache reset, account switch, sign-out, schema migration, or DB-version-mismatch handler — that calls `unlink()` (or `NSFileManager removeItemAtURL:`) on `Library/zenmoney.sqlite` (and the `-shm` / `-wal` siblings) **before** closing the SQLite handles.
2. libsqlite3 detects the unlink-while-open and trips its `BUG IN CLIENT OF libsqlite3.dylib … vnode unlinked while in use` integrity guard. The fds are invalidated, so any subsequent read of cached user credentials returns nothing.
3. The poller / sync layer asks the cache for the current user, gets nothing back, and aborts with `Missing user details, aborting makePollerRequest`.
4. With no authenticated requests possible, the UI surfaces the generic "Unable to connect to server" error, even though the network and your backend are healthy.

A few hypotheses worth investigating on your side, in priority order:

- **Concurrent unlink + active read.** Look for any code path in the iOS app that removes `zenmoney.sqlite*` files (e.g. `clearLocalDatabase`, `resetCache`, `onLogout`, `migrateDatabase`, `resetForNewUser`). Verify that **every** caller of that path first closes all `sqlite3*` handles (and any Core Data / GRDB / FMDB pools) and waits for completion before issuing the unlink. The fact that this reproduces on a **cold install of the Production build** suggests the responsible code path runs on first launch — possibly a "if database version mismatch / first-run / no user → wipe and recreate" branch.
- **Race between background sync and a foreground reset.** The `Missing user details` error fires during the same window as background NWPath connection state changes. If the foreground UI thread issues the unlink while a background queue is mid-query, libsqlite3 will trip exactly this guard.
- **Stale `sqlite_open_v2` handle leak.** A previous session that ended without a clean `sqlite3_close_v2` would leave handles in `Caches/` that get unlinked on the next launch's "clean state" sweep.
- **Unrelated to the above:** the TCP RSTs from `Hostname#00e493d7:443` mid-session may indicate that one of your backend hosts is dropping idle/unauthenticated connections. Worth a separate look on your edge but **not** the cause of this ticket — those resets only happen *after* the local SQLite is already toast.

I do **not** think this is the same bug as the iOS support thread from a few weeks ago about HTTP 500 from the OAuth WebView. There is **zero** WebKit / `WebResourceLoader … httpStatusCode=500` activity in any of these four captures. That earlier hypothesis appears to be obsolete.

---

## What I tried, and what I'd like from you

Tried locally, did not help:
- App relaunch (foreground / background → foreground).
- Force-quit + relaunch.
- Reinstall of the App Store Production build (the cold install in `error_log_1.md` is exactly that — the bug fires within 50 s).
- Switching between Production and TestFlight builds — both reproduce.

Workaround in use: I've fallen back to the web app and a private CLI that talks to `https://zenmoney.ru/api` over PHPSESSID, which works fine. So the *backend* is unambiguously reachable and serving authenticated requests for this account.

What would help from your side:
1. Confirmation that you can reproduce `BUG IN CLIENT OF libsqlite3.dylib … vnode unlinked while in use` against a fresh-install run of `26.2 (625)`.
2. Identification of the iOS code path that unlinks `Library/zenmoney.sqlite*` (it's almost certainly grep-able for `unlink`, `removeItem`, or the file name).
3. A timeline for a `26.2.1` / next build with the lifecycle fix.
4. If useful, I can run a fresh capture with more specific log filters (e.g. `subsystem:ru.zenmoney.ZenMoneyNative` only) — let me know what you'd like me to grab.

---

## Attachments

- `eror_log.md` — warm relaunch, TestFlight, ~280 KB
- `error_log_1.md` — **cold install**, **Production** build, ~993 KB (contains the cleanest reproduction signature)
- `error_log_2.md` — warm relaunch via TestFlight handoff, ~1.5 MB
- `error_log_3.md` — focused warm-relaunch capture of the failure window, ~73 KB
- `scripts/detect_zenmoney_log.py` — local Python script that classifies the captures (http_error / missing_user_details / sqlite_api_violation / connection_reset / post_tls_lower_stack_error / etc.) — happy to share if useful for your QA.
