# Fleet deployment (MDM) — Claude Code usage telemetry

Two artifacts get pushed to every developer machine. **Both are required** —
one turns telemetry on, the other tags it with the repo.

| File | Job | Nature |
|---|---|---|
| `managed-settings.json` | Turn telemetry **ON** (enforced) and point it at the billing receiver | **Static** — same on every machine |
| `claude-wrapper.sh` | Tag each session with `repo=<git remote>` | **Dynamic** — computed per session (see that file) |

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
   `/usr/local/bin/claude` (mark executable).
3. Set `CLAUDE_REAL_BIN=/opt/cyclotron/claude-real` (in `managed-settings.json`'s
   `env`, or a system profile). The wrapper execs that binary directly.

If `CLAUDE_REAL_BIN` is unset the wrapper still works — it discovers the real
binary on the PATH (skipping itself) then common install locations — but pinning
`CLAUDE_REAL_BIN` is the robust choice for a fleet.

### Verify on a machine
```
cd <any git repo>
CLAUDE_REAL_BIN=/usr/bin/env claude | grep OTEL_RESOURCE_ATTRIBUTES
# → OTEL_RESOURCE_ATTRIBUTES=...,repo=<that repo's remote>
```

---

## 3. Hosting the receiver (server side)

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
