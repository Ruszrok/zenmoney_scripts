# ZenMoney iOS App Login Failure — Debug Notes

**Date:** 2026-04-08
**Status:** Unresolved (server-side issue)
**Affected:** iOS app only. Android and web (zenmoney.ru) work fine.

## Symptom

ZenMoney iOS app shows "Unable to connect to server" and cannot log in. Fresh install + reinstall does NOT fix it.

## Root Cause

### 1. HTTP 500 on Login Page (SERVER-SIDE)

The app opens a SafariViewService WebView for OAuth login. The server returns **HTTP 500**:

```
15:51:15.814836 WebResourceLoader::didReceiveResponse: (httpStatusCode=500)
```

The login page loads only 155 bytes (error page), finishes, and the app has no user credentials.

### 2. Missing User Details (consequence of #1)

```
15:52:51.606930 Missing user details, aborting makePollerRequest
```

Since login failed, the app can't find stored credentials and refuses to sync.

### 3. SQLite Database Corruption (APP BUG)

```
15:53:26.306333 BUG IN CLIENT OF libsqlite3.dylib: database integrity compromised by API violation:
  vnode unlinked while in use: .../Library/zenmoney.sqlite
```

This reproduces on fresh installs — it's a bug in the ZenMoney app's SQLite handling, not caused by leftover data.

**Database path:**
```
/private/var/mobile/Containers/Data/Application/<UUID>/Library/zenmoney.sqlite
```

## What's NOT the Problem

- **Network connectivity** — all TLS 1.3 and QUIC connections succeed normally
- **DNS resolution** — resolves instantly
- **Certificate validation** — all trust evaluations pass
- **App installation** — app installs and launches fine from App Store
- **iOS sandbox** — `deny(1) sysctl-read kern.bootargs` errors are normal and harmless

## Key Log Lines to Search For

When debugging future login issues, search iOS logs (`log stream`) for:

```bash
# Filter for Zenmoney process
log stream --predicate 'process == "Zenmoney"' --level debug

# Key patterns to grep:
grep "httpStatusCode"          # Check for non-200 responses
grep "Missing user details"    # Login credential check
grep "BUG IN CLIENT"           # SQLite corruption
grep "WebResourceLoader"       # WebView resource loading
```

## How to Capture iOS Logs

From a Mac connected to iPhone via USB:

```bash
# Stream all logs for Zenmoney app
log stream --predicate 'process == "Zenmoney" OR process == "SafariViewService"' --level debug

# Or capture to file
log stream --predicate 'process == "Zenmoney"' --level debug > zenmoney_logs.txt
```

Reproduce the login attempt while streaming, then stop with Ctrl+C.

## How to Extract the SQLite Database

1. Install iMazing: `brew install --cask imazing`
2. Connect iPhone via USB
3. In iMazing File System > Apps > Zenmoney — if `Library/` folder is visible, copy `zenmoney.sqlite` directly
4. If not accessible, make a full backup first, then browse the backup to find the file

## Timeline of Login Flow (from logs)

1. **15:51:12** — App launched, Metal shaders compiled
2. **15:51:14** — WebView (WebContent, GPU, Networking processes) launched for login
3. **15:51:14** — TLS 1.3 handshake succeeds, first API call returns 200
4. **15:51:15** — Login page WebView loads → **HTTP 500** (155 bytes)
5. **15:51:19** — SafariViewService opens (OAuth login UI with keyboard)
6. **15:51:20** — Firebase Analytics initialized, ATTrackingManager checked
7. **15:52:51** — "Missing user details, aborting makePollerRequest"
8. **15:53:26** — SQLite corruption errors on zenmoney.sqlite

## Resolution

This is a **ZenMoney server-side issue**. Their iOS OAuth login endpoint returns HTTP 500. Contact ZenMoney support. Android likely uses a different login endpoint/flow which is why it works.

## Bundle Info

- Bundle ID: `ru.zenmoney.ZenMoneyNative`
- App container example: `B397D0C4-DA26-4F0E-A66D-80D9A0EC7EA7`
- Data container example: `11C02CB4-84FE-4FA3-942D-DBC7265C1DF6`
