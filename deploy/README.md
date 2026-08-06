# Fleet deployment (MDM) — Claude Code usage telemetry

Two artifacts get pushed to every developer machine. **Both are required** —
one turns telemetry on, the other tags it with the repo.

| File | Job | Nature |
|---|---|---|
| `managed-settings.json` | Turn telemetry **ON** (enforced), point it at the billing receiver, register the hook | **Static** — same on every machine |
| `claude-wrapper.sh` | Tag each session with `repo=<git remote>` at launch | **Dynamic** — computed per session (see that file) |
| `claude-repo-tag.py` | Record repo changes **during** a session (hook) | **Dynamic** — fires on every `cd` |

`managed-settings.json` alone is necessary but **not sufficient**: without the
wrapper, sessions emit usage with no repo tag, and it all lands in the
`unknown` bucket — unattributable. Deploy both.

## 1. managed-settings.json

### What each setting does
| Key | Why |
|---|---|
| `CLAUDE_CODE_ENABLE_TELEMETRY=1` | Master switch: emit OpenTelemetry metrics. |
| `OTEL_METRICS_EXPORTER=otlp` | Send metrics via OTLP (this is what carries `claude_code.token.usage`). |
| `OTEL_LOGS_EXPORTER=none` | Billing needs metrics only — don't ship event logs (less traffic, less data exposure). |
| `OTEL_EXPORTER_OTLP_PROTOCOL=http/json` | Matches the billing receiver. If you front the receiver with an OpenTelemetry Collector, you can switch to the more efficient `http/protobuf`. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | **Replace the placeholder** with your hosted receiver/collector URL (HTTPS, reachable from dev machines over VPN/network). |
| `OTEL_METRIC_EXPORT_INTERVAL=60000` | Export once a minute — fine for billing (not real-time). |
| `OTEL_METRICS_INCLUDE_SESSION_ID=true` | Keeps `session.id` on records (used for dedupe). |

### Where the file goes (the enforced, system-level path)
Placing it here (not in a user directory) is what makes it **enforced** —
system managed settings outrank user settings, so developers can't disable
telemetry. Confirm the exact path for your Claude Code version.

| OS | Path |
|---|---|
| macOS | `/Library/Application Support/ClaudeCode/managed-settings.json` |
| Linux / WSL | `/etc/claude-code/managed-settings.json` |
| Windows | `C:\ProgramData\ClaudeCode\managed-settings.json` |

Push it there via your MDM (Jamf / Intune / Kandji). New hires inherit it
automatically.

### Verify on a machine
```
# telemetry env is present and enforced:
claude --version              # confirms CLI present
# then run a session in a repo and confirm records reach the receiver
```

## 2. claude-wrapper.sh (repo tagging)

Deployed as the `claude` entrypoint so every CLI session is stamped with its
git remote (`OTEL_RESOURCE_ATTRIBUTES=repo=<remote>`). The receiver normalizes
that remote and maps it to a client. Sessions started outside a git repo tag as
`repo=unknown` and surface in the `unknown` bucket (flagged, never silently mis-billed).

> The VS Code extension does **not** export OTEL telemetry, so repo-attributed
> billing standardizes on CLI usage. Direct developers to use the `claude` CLI.

### Install (recommended, deterministic)
Avoids PATH-ordering fragility and the wrapper-calls-itself trap:

1. Install the real Claude Code binary at a fixed path **off** the PATH, e.g.
   `/opt/cyclotron/claude-real`.
2. Push this script as the **only** `claude` on the PATH, e.g.
   `/usr/local/bin/claude` (mark executable). Exactly one copy — see the
   one-copy rule below.
3. Set `CLAUDE_REAL_BIN=/opt/cyclotron/claude-real` **in the shell environment**
   — a system profile (`/etc/profile.d/`, `/etc/zshenv`) or your MDM's
   environment payload. The wrapper execs that binary directly.

> ⚠️ **`CLAUDE_REAL_BIN` must NOT be set in `managed-settings.json`'s `env`.**
> That file is read by Claude Code and applied to *its own* process. The wrapper
> is Claude Code's **parent** — it runs and finishes its work before Claude Code
> exists — so a variable set there can never reach it. Put it there and the
> wrapper silently sees nothing and falls through to auto-discovery, i.e. you
> get none of the determinism this section is for. It must come from the shell.

If `CLAUDE_REAL_BIN` is unset the wrapper still works — it discovers the real
binary on the PATH (skipping any copy of itself) then common install locations —
but pinning `CLAUDE_REAL_BIN` is the robust choice for a fleet.

### The one-copy rule
**Ship exactly one copy of `claude-wrapper.sh` per machine.** A symlink to it
elsewhere on the PATH is fine; a second *copy* is not.

The wrapper recognises itself by the marker string
`__CYCLOTRON_CLAUDE_WRAPPER__` in its own source, so it will detect and skip a
copy at any path and refuse a `CLAUDE_REAL_BIN` that points at itself. Before
that marker existed it compared realpaths, which can't distinguish two separate
copies — each execs the other, and because `exec` replaces the process rather
than forking, the session hangs in one spinning process with no error output.
The marker plus a `CLAUDE_WRAPPER_DEPTH` re-entry guard now turn that into an
immediate exit 127 with a message, but the underlying install mistake is still
worth avoiding: don't let MDM write the wrapper to two directories.

Do not remove the marker comment from the script.

### Verify on a machine
```
cd <any git repo>
CLAUDE_REAL_BIN=/usr/bin/env claude | grep OTEL_RESOURCE_ATTRIBUTES
# → OTEL_RESOURCE_ATTRIBUTES=...,repo=<that repo's remote>

# confirm exactly one COPY of the wrapper is on the PATH:
IFS=':'; for d in $PATH; do
  [ -f "$d/claude" ] && grep -q __CYCLOTRON_CLAUDE_WRAPPER__ "$d/claude" 2>/dev/null && ls -ld "$d/claude"
done; unset IFS
# → exactly one regular file (-rwx…). Extra `l…  … -> …` symlink lines are fine;
#   a second regular file is the misinstall that used to hang sessions.
```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `claude` hangs forever, no output, one process at high CPU | Two *copies* of the wrapper on the PATH exec'ing each other | Run the `grep -rl` check above; leave one copy. Current wrapper exits 127 with a message instead of hanging. |
| `claude-wrapper: re-entered itself…` | Same as above, caught by the depth guard | Same as above. |
| `claude-wrapper: could not find the real claude binary` | Wrapper is the only `claude`, or the real binary isn't executable/installed | Set `CLAUDE_REAL_BIN` in the shell environment (not managed-settings.json). |
| `claude-wrapper: CLAUDE_REAL_BIN=… is this wrapper` | `CLAUDE_REAL_BIN` points at the wrapper instead of the real binary | Point it at the real binary, e.g. `/opt/cyclotron/claude-real`. |
| All usage lands in `repo=unknown` | Sessions started outside a git repo, or the repo has no `origin` remote | Expected — the `unknown` bucket is flagged, never silently mis-billed. |
| Usage tagged with the *wrong* repo | A user- or project-level `settings.json` pins `OTEL_RESOURCE_ATTRIBUTES` in its `env`, which overrides the wrapper's value | Remove the static `OTEL_RESOURCE_ATTRIBUTES` from that settings file. Note this repo's own `.claude/settings.local.json` pins one deliberately for local self-testing — that's dev-only, don't copy the pattern to a fleet machine. |

---

## 3. claude-repo-tag.py (mid-session repo switching)

The wrapper alone gets the repo **wrong** whenever a developer changes repos
mid-session. `OTEL_RESOURCE_ATTRIBUTES` is a *resource* attribute: the OTEL SDK
reads it once at startup and it is immutable for the process lifetime. So a
session that starts in *acme-portal* and `cd`s into *globex-api* bills **all**
of it to acme-portal — including the work done for the other client.

No metric or event carries the working directory (paths are deliberately kept
out of Claude Code's telemetry), so the repo cannot be recovered from the
telemetry stream. A hook is the only signal that sees the transition.

`claude-repo-tag.py` runs on `SessionStart`, `CwdChanged`, `DirectoryAdded`, and
`SessionEnd`. Claude Code passes it `session_id` + `cwd` on stdin; it resolves
the git remote and POSTs one timeline entry to `POST /v1/session-repo`. At
billing time `billing/otel/attribute.py` joins that timeline back onto each
usage datapoint by `session_id` + `ts` (an "as-of" join), so usage splits across
the repos it was actually done in.

Hooks also run on **every** Claude Code surface — CLI, IDE extension, desktop,
web — not just the CLI the wrapper shims.

### Install

1. Push `claude-repo-tag.py` to a fixed path, e.g. `/opt/cyclotron/claude-repo-tag.py`,
   and mark it executable (`chmod +x`).
2. Register it under `hooks` in `managed-settings.json` (already wired in this
   repo's copy) so it's enforced org-wide and developers can't drop it.
3. Point it at the receiver with `CLAUDE_BILLING_RECEIVER`.

> ✅ **Unlike `CLAUDE_REAL_BIN`, `CLAUDE_BILLING_RECEIVER` *does* belong in
> `managed-settings.json`'s `env`.** That file's `env` applies to Claude Code
> and to the subprocesses it spawns — and hooks *are* such subprocesses. The
> wrapper is Claude Code's **parent**, which is why its variable can't come from
> there. Same file, opposite answer; the direction of the process tree is what
> decides it.

**Requires `python3` on the PATH** (stdlib only — no pip installs). If it's
missing the hook exits non-zero, which Claude Code treats as a non-blocking
error: you lose timeline entries, sessions keep working.

### Design rule: this hook can never break a session

It always exits 0 and never exits 2 (exit 2 would *block* the tool call).
Network failures, malformed stdin, and missing `git` are all swallowed. A lost
timeline entry degrades attribution for one export interval; a blocked tool call
breaks someone's work.

### Verify on a machine

```
# fire the hook by hand and confirm the receiver records it
echo '{"session_id":"test-1","cwd":"'"$PWD"'","hook_event_name":"SessionStart"}' \
  | CLAUDE_BILLING_RECEIVER=http://127.0.0.1:4318 /opt/cyclotron/claude-repo-tag.py
echo "exit=$?"   # MUST be 0

# server side: the entry landed
sqlite3 ./otel-data/otel.db \
  "SELECT session_id, ts, repo, event FROM session_repo_timeline ORDER BY ts DESC LIMIT 5;"
```

Then `python -m billing.otel.bill` reports an **ATTRIBUTION SOURCE** breakdown
(`timeline` / `wrapper` / `no_remote` / `absent`) and flags every multi-repo
session, so you can see how much of the bill each signal is carrying.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `attribution_source` is all `wrapper` | Hook not firing — not installed, not executable, or not registered in managed settings | Run the verify command above; check the path in `managed-settings.json` |
| All `absent` for some users | Those sessions never passed through the wrapper *or* the hook — likely a non-CLI surface | Confirm the hook is in **managed** settings (applies to all surfaces), not just user settings |
| Hook exits non-zero | `python3` missing on the PATH | Install it, or rewrite the hook for an interpreter you do ship |
| Timeline entries exist but usage still bills to one repo | Datapoints and timeline don't share a `session_id` — check `OTEL_METRICS_INCLUDE_SESSION_ID` is `true` | It's `true` by default; don't set it to `false` |
| Multi-repo session split looks wrong by a small amount | A repo switch inside one 60s export interval lands wholly on one side | Lower `OTEL_METRIC_EXPORT_INTERVAL` to tighten the window (more traffic) |

---

## 4. Hosting the receiver (server side)

The receiver is the endpoint `OTEL_EXPORTER_OTLP_ENDPOINT` points at. It must run
somewhere reachable from dev machines. Containerized via the repo-root
`Dockerfile` / `docker-compose.yml`:

```
docker compose up -d --build     # from the repo root
```

- Listens on `:4318` for OTLP/JSON; captures `claude_code.token.usage` and
  `claude_code.cost.usage`, tagged with the repo.
- SQLite store + request log persist in `./otel-data` on the host.
- Stdlib-only image (no dependencies).

**Production hardening:**
- **TLS** — dev machines should hit `https://…`, not raw `:4318`. Front the
  receiver with a reverse proxy (Caddy/nginx) or an OpenTelemetry Collector that
  terminates TLS and forwards to `receiver:4318`. Set
  `managed-settings.json`'s `OTEL_EXPORTER_OTLP_ENDPOINT` to that HTTPS URL.
- **Durability** — for a large fleet, put an OpenTelemetry Collector in front
  (buffering/retry) so a receiver restart doesn't drop telemetry (= lost
  revenue). The receiver still parses what the Collector forwards.
- **Backups** — back up `./otel-data/otel.db`; it's the source of billing truth.

Then run the billing loop against the store the receiver writes:
`bill → reconcile → invoice` (see the top-level README).
