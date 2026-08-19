# internal-billing-engine

Backend engine to obtain Claude Code usage data and its link with its associated GitHub repositories. Attributes Cyclotron employees' Claude usage to the repository it was done in, and in turn to the client it was done for. 

Can run locally because of .claude > settings.local.json, settings (deploy/) will have to be pushed to all dev machines in order for global telemetry to be captured.

No third-party Python dependencies — standard library only, Python 3.10+. Nothing
to `pip install`.

---

## Quick start

**Which one are you?**

| You want to… | Go to |
|---|---|
| Enrol *your own* machine (opt-in, one click) | [`client-package/INSTRUCTIONS.md`](client-package/INSTRUCTIONS.md) |
| Send the package to developers | [`client-package/ADMIN.md`](client-package/ADMIN.md) |
| Push telemetry to the whole fleet via MDM | [`deploy/README.md`](deploy/README.md) |
| Run the receiver / produce invoices | below |
| Turn the pushed CSVs into Fabric tables | [`fabric/README.md`](fabric/README.md) |

### Run the whole pipeline locally (~5 minutes, no Docker, no Azure, no config)

Proves the chain end to end on one machine before any of it touches a fleet.
Nothing here needs a `.env` — the receiver runs open on localhost, which is the
one place that's fine.

Use `python3` on macOS/Linux, `python` on Windows.

> **Windows, two one-time gotchas.** Set `PYTHONUTF8=1` first — the reporting
> commands print `⚠` and `→`, which crash with `UnicodeEncodeError` on a cp1252
> console. And if `python` opens the Microsoft Store instead of running, that's the
> App Execution Alias stub, not a Python: install a real one with
> `winget install --id Python.Python.3.12 --scope user`, or just use
> `.\deploy\start-local.ps1`, which finds the real interpreter itself.
>
> ```powershell
> $env:PYTHONUTF8 = "1"
> ```

```bash
# 1. Start the receiver. Leave it running; open a second terminal for the rest.
python3 -m billing.otel.receiver
#    Windows alternative (finds Python for you):  .\deploy\start-local.ps1 -Open

# 2. Push synthetic telemetry through it — six sessions, five repos, one with no
#    git remote, and two that are the same repo written two ways.
python3 -m billing.otel.sample_payload

# 3. See what it made of them.
python3 -m billing.otel.bill                                      # per-repo totals + attribution
python3 -m billing.otel.invoice --start 2026-07-01 --end 2026-08-01
```

The sample data is stamped **2026-07-14**, so the invoice window above is the one
that returns rows — an empty invoice usually means the dates missed the data, not
that ingest failed. Invoices land in `invoices/`, the store in `data/otel.db`.

To exercise the auth path too, put a token in `.env` and restart the receiver with
`--require-auth`. Note that `sample_payload.py` sends no token, so it will get a
`401` against an enforcing receiver — that's the expected result, and it's the
quickest way to confirm enforcement is actually on. Use a real Claude Code session
(below) to generate authenticated traffic.

### Run the receiver for real (Docker)

```bash
cp .env.example .env        # set RECEIVER_AUTH_TOKEN; add ADLS_*/AZURE_* to sync to the lake
docker compose up -d --build
docker compose logs -f receiver     # should print: auth=ENABLED
```

Data persists in `./otel-data`. Two things to get right before anyone else points
at it: put TLS in front (the Caddy sidecar is commented into
`docker-compose.yml`), and set `RECEIVER_AUTH_TOKEN` — `auth=DISABLED` in that log
line means anyone who can reach the port can write billing rows.

### Feed it your own usage instead of synthetic data

`.claude/settings.local.json` (gitignored) points this machine at a local receiver,
so a `claude` session in this repo produces real rows. `deploy/dev-selftest.sh` does
the same as a one-off launcher without changing your settings. Telemetry starts with
the **next** session, and exports every 60s — so give it a minute before checking.

---

## 1: Receiving telemetry data
The engine gets data via an OTLP/JSON receiver (receiver.py) that ingests telemetry emitted by Claude Code on each dev machine. Deduped datapoints are stored in a single-host SQLite database.

The same receiver also accepts `POST /v1/session-repo` — not an OTLP endpoint, but the session→repo timeline written by the `claude-repo-tag.py` hook. Claude Code freezes `OTEL_RESOURCE_ATTRIBUTES` at launch, so the wrapper's `repo=` tag can't follow a developer who `cd`s into another client's repo mid-session; the timeline can, and `attribute.py` joins it back onto each datapoint at billing time. Writes are authenticated with a shared fleet token (`RECEIVER_AUTH_TOKEN`).

## 2: Push data to ADLS
A scheduler (scheduler.py) regenerates two flat all-history CSVs (claudeuseagesummary, claudeusagelineitems) and enqueues them. Sync worker (fabric_sync.py) drains outbox and uploads to ADLS (rg-cyclotron-insights > sacyclotroninsights > cyclotroninsights), overwriting each file. Scheduled daily but can be modified (SYNC_FREQUENCY in .env).

## 3: Load into Fabric as Delta tables
Connection through service principal allows for shortcut into CyclotronInsights workspace's lake_insights_db lakehouse > Files. claude-billing-push notebook scheduled to lake_insights_db > Tables as claudeusagesummary and claudeusagelineitems.

## 4: Generate invoices/reports
Notebook in CyclotronInsights workspace in Fabric filters claudeusagelineitems by period_start and period_end (month), joins with repoclientmap, transforms it by aggregating tokens/cost group by client, repo, and model, and produces itemized invoice.
- MAPPING TABLE: https://cyclotron-my.sharepoint.com/:x:/p/zane_ching/IQAmpiRQlK8hQqW8CXAGeqYlAd4Ip-Yx8hDdjhva87tv6rs?e=xfOWfi
- connected with Fabric through dataflow, updated daily @ 23:59 PST

## Folder layout

```
billing/            Python package — both pipelines + reconciliation
  otel/             the OTEL (repo-level) path
deploy/             client-side config pushed to dev machines (MDM, enforced)
client-package/     opt-in, one-click installer developers run themselves
pilot-package/      the earlier opt-in package — superseded by client-package/
fabric/             Fabric-side notebook + docs (CSV → Delta tables)
data/               generated SQLite stores + logs (gitignored)
.claude/            local Claude Code settings (gitignored)
Dockerfile          container image for the receiver
docker-compose.yml  receiver + sync worker services
```

There are **two enrolment tracks** onto the same receiver: the enforced MDM track
(`deploy/`) and the opt-in double-click track (`client-package/`). They install the
same hook and the same telemetry env; they differ only in who applies it and
whether a developer can remove it.

### `billing/` — the engine

Shared:
- **`__init__.py`** — marks `billing/` (and `billing/otel/`) as Python packages; no code.
- **`config.py`** — loads `.env` into the environment so tools find the API token.

**Analytics path (per-user):**
- **`analytics_client.py`** — client for the Anthropic Analytics API (`usage_report`, `cost_report`, `user_usage_report`); auth, 31-day windowing, pagination
- **`ingest.py`** — pulls a date range from the Analytics API into the store.
  `python -m billing.ingest --start 2026-07-01 --end 2026-07-15`
- **`store.py`** — SQLite store for Analytics data + the `tokens()` field extractor.
- **`report.py`** — prints what the Analytics data reveals (product/model/user breakdown, cost) and where it can't attribute (no repo dimension).

**Bridge:**
- **`reconcile.py`** — compares OTEL-captured tokens against the authoritative Analytics total as a funnel (truth → captured → repo-tagged). Repo-tagged usage is billable, so that's the last stage.
  This is the acceptance test / coverage metric for the OTEL rollout.
  `python -m billing.reconcile --start 2026-07-14 --end 2026-07-15`

### `billing/otel/` — the OTEL (repo-level) path

- **`receiver.py`** — minimal OTLP/JSON HTTP server. Accepts `claude_code.token.usage` and `claude_code.cost.usage` from Claude Code (handles chunked + gzip bodies), extracts repo/user/model/token-type, dedupes, writes to the store. Also accepts `POST /v1/session-repo` from the repo-tag hook. Every POST must present the shared fleet token (`X-Billing-Token` or `Authorization: Bearer`) once `RECEIVER_AUTH_TOKEN` is set; unset means open, with a loud startup warning.
  `python -m billing.otel.receiver` (`--host`, `--port`, `--db`, `--require-auth`)
- **`attribute.py`** — resolves *which repo a datapoint bills to*, at query time. Joins the `session_repo_timeline` onto each datapoint as-of its own timestamp, so a session that moved between repos splits across them. Falls back through `timeline → wrapper → no_remote → absent`, and exposes that choice as `attribution_source` so you can see which signal is carrying the bill. Resolution is derived, never stored: a late or corrected timeline retroactively fixes past bills with no re-ingest.
- **`otel_store.py`** — SQLite store: deduped `token_usage` and `cost_usage` datapoints, the `session_repo_timeline`, persisted invoices + line items, the optional `repo_name_map` override table, and the `fabric_outbox` delivery queue.
- **`normalize.py`** — collapses git remote forms (ssh vs https, `.git`, case) into one canonical repo key so a repo isn't billed twice, and derives the short repo name (`repo_name`) that is the billing identity.
- **`repos.py`** — manage the OPTIONAL repo→billing-name override map: `export` observed repos to CSV, edit the `bill_name` column to rename/group a repo, then `import`. Not needed by default — every repo bills under its own name.
  `python -m billing.otel.repos export --out repo_name_map.csv`
- **`rating.py`** — `RatingService`: token counts → billable USD (per-model rates × markup, cache-token multipliers) RATES ARE PLACEHOLDERS AND NEED TO BE REPLACED W/ REAL PRICES
- **`bill.py`** — aggregates usage → repo, bills on actual cost (`claude_code.cost.usage`) × markup with a rate-card cross-check; flags `unknown` usage (sessions with no git remote) and prints an **ATTRIBUTION SOURCE** breakdown plus every multi-repo session. `python -m billing.otel.bill`
- **`invoice.py`** — generates per-repo invoices for a billing period: persists immutable invoice + line-item records and writes a human-readable `.txt` invoice + `summary.csv` / `line_items.csv` under `invoices/`. `python -m billing.otel.invoice --start 2026-07-01 --end 2026-08-01`
- **`records.py`** — dumps individual usage records with their repo tag + resolved billing name.
- **`sample_payload.py`** — generates a synthetic OTLP payload to exercise the pipeline without live machines.

**Data-lake sync (asynchronous):**
- **`fabric_client.py`** — uploads a file to **ADLS Gen2** (or **OneLake**) via the ADLS Gen2 DFS REST API; Entra service-principal / managed-identity / SAS auth (stdlib only). Idempotent overwrite (create → append → flush).
- **`export.py`** — builds the two **running, all-history** lake tables (`claudeusagesummary`, `claudeusagelineitems`) from the store: flat single CSVs (no date-partition folders) with the usage month + `generated_at` as columns, regenerated in full each sync.
- **`fabric_sync.py`** — drains the delivery outbox: ships queued CSVs with retry/backoff. `python -m billing.otel.fabric_sync` (run-once; `--watch`, `--status`, `--dry-run`).
- **`scheduler.py`** — the periodic sync job: regenerate the current month (month-to-date) and ship it, at the cadence in `SYNC_FREQUENCY` (hourly/daily/weekly/monthly). `python -m billing.otel.scheduler` (once; `--loop`, `--emit-cron`, `--month` backfill).

### `deploy/` — client-side rollout (pushed via MDM, enforced)

- **`managed-settings.json`** — enforces telemetry ON, the exporter endpoint, the
  fleet billing token, and the repo-tag hook registration. Placed at the
  system-level managed-settings path so developers can't disable it. Ships with
  `REPLACE_WITH_FLEET_BILLING_TOKEN` placeholders — MDM substitutes the real
  token at deploy time; **never commit the real value.**
- **`claude-wrapper.sh`** — the `claude` entrypoint on each machine; stamps every
  session with `OTEL_RESOURCE_ATTRIBUTES=repo=<git remote>` at launch.
- **`claude-repo-tag.py`** — the hook that keeps attribution correct *during* a
  session. Fires on `SessionStart`, `CwdChanged`, `DirectoryAdded`, `SessionEnd`
  and (async) `UserPromptSubmit`, POSTing one timeline entry per repo change to
  `/v1/session-repo`. Unlike the wrapper it runs on **every** Claude Code surface,
  not just the CLI. Designed never to break a session: always exits 0.
- **`dev-selftest.sh`** — scoped launcher to generate real telemetry from one
  machine into a local receiver (for testing before a fleet rollout).
- **`start-local.ps1`** — runs the receiver directly on a Windows host, no Docker.
  `.\deploy\start-local.ps1 [-Port 4318] [-BindAll] [-Open]`
- **`run-sync.sh`** — one-shot sync wrapper for cron/systemd: refresh the current
  month and ship the CSVs (the `sync` compose service runs the self-pacing loop instead).
- **`README.md`** — deployment instructions for IT (paths, enforcement, verify,
  troubleshooting).

### `client-package/` — opt-in rollout (developers install it themselves)

The distributable, double-click alternative to the MDM track — same receiver, same
hook, no admin rights, fully reversible. Built into `client-package.zip` at the
repo root by `build.py`, which bakes the endpoint + token into the archive so
developers never open a terminal.

- **`ADMIN.md`** — for whoever owns the rollout: how to build, how to distribute
  (the zip carries a live token, so it *is* a credential), and what's still
  unfinished.
- **`INSTRUCTIONS.md`** — developer-facing: install, what's collected, verify, uninstall.
- **`build.py`** — builds the zip and refuses to package a source file containing
  an API key or 64-char hex token. Bump `VERSION` when anything here changes.
- **`configure.py`** — the actual install/uninstall/verify logic, shared by both
  platforms; merges into an existing `~/.claude/settings.json` atomically, with a
  timestamped backup. The launchers and shims are wrappers around this — if you
  add a platform, write another shim rather than reimplementing the merge.
- **`Install`/`Verify`/`Uninstall` `.command` / `.bat`** — the one-click launchers
  (macOS / Windows), wrapping `install.sh` / `install.ps1`.

### `pilot-package/` — superseded

The original opt-in package: bash-only, refused to touch an existing
`~/.claude/settings.json`, and set `OTEL_LOGS_EXPORTER=none` (which Claude Code
rejects — see `deploy/README.md`). Kept for reference. **Send anyone enrolling
today the `client-package/` build instead**; running its installer also cleans up
the bad key on machines that ran the pilot.

### `data/` — generated (gitignored)

- **`otel.db`** — OTEL token usage + optional repo→billing-name override map.
- **`analytics.db`** — data pulled from the Analytics API.
- **`receiver.log`** — every request the receiver handled (diagnostics).

### `.claude/` — local Claude Code settings (gitignored)

- **`settings.local.json`** — personal telemetry config used when testing this
  machine as a telemetry source (points Claude Code at a local receiver).

### `fabric/` — Fabric-side (CSV → running Delta tables)

The engine pushes two flat, all-history CSVs to ADLS; these turn them into
queryable Delta tables inside Microsoft Fabric.

- **`README.md`** — the Fabric setup runbook: shortcut the CSVs into **Files**
  (not Tables), then convert each into a Delta table (one-off in the UI, or the
  scheduled notebook below).
- **`refresh_billing_tables.py`** — Fabric notebook cell that overwrites both
  Delta tables (`claudeusagesummary`, `claudeusagelineitems`) from the full-history
  CSVs each run; schedule it to fire shortly after the sync job.

### Container & compose

- **`Dockerfile`** — image for the telemetry receiver (stdlib-only, `python:3.12-slim`);
  persists the store + request log on a mounted `/data` volume, exposes `4318`.
- **`docker-compose.yml`** — hosts the `receiver` service plus the long-running
  `sync` worker (self-pacing scheduler shipping CSVs to the lake), sharing one
  `./otel-data` volume; includes a commented Caddy TLS sidecar for production.
- **`.dockerignore`** — keeps secrets, data, and local settings out of the build context.

### Repo hygiene

- **`.gitignore`** — excludes secrets (`.env*`), generated data/stores, invoices,
  and personal `.claude` settings from version control.
- **`.gitattributes`** — marks `*.zip` binary so no line-ending conversion can
  corrupt a distributable archive on checkout. `client-package/.gitattributes`
  additionally pins LF on the shell/Python files and CRLF on `.ps1`/`.bat`, because
  a CRLF `install.sh` fails on macOS before it does anything.

---

## Config & secrets

- **`.env`** (gitignored) — copy from **`.env.example`** (placeholders only). Holds:
  - `ANTHROPIC_ANALYTICS_TOKEN` — for the Analytics API path.
  - `RECEIVER_AUTH_TOKEN` — the shared fleet token the receiver checks on every
    POST. Must match what dev machines send (`OTEL_EXPORTER_OTLP_HEADERS` +
    `CLAUDE_BILLING_TOKEN`). Generate with `openssl rand -hex 32`. **Blank means
    the receiver runs open** — see the rollout order in `deploy/README.md`.
  - `SYNC_*` / `ADLS_*` / `ONELAKE_*` / `AZURE_*` — the data-lake target and auth.
- **`repo_name_map.csv`** — optional repo→billing-name overrides (editable working file; only needed to rename or group repos).

## Typical OTEL flow

```
receiver.py (ingest telemetry + session→repo timeline)
        ↓
attribute.py (as-of join: which repo was active per datapoint)
        ↓
bill  →  reconcile (coverage check)  →  invoice (per period)

optional, only to rename/group repos:  repos export  →  edit bill_name  →  repos import
```

## Shipping invoices to a data lake (Fabric / ADLS Gen2)

Usage is pushed to a data lake as **two running, all-history tables**,
**asynchronously and durably**:

```
scheduler → export.build (all history) → enqueues to fabric_outbox (pending)
fabric_sync → drains the outbox → uploads to ADLS Gen2 / OneLake (retry + backoff) → marks sent
```

- **The two tables** (each a single flat CSV, no date folders, so Fabric loads it
  as one running table):
  - `claudeusagesummary.csv` — one row per (usage date, repo/bill_name, user_email)
  - `claudeusagelineitems.csv` — one row per (usage date, repo, model, user_email)
- **Date is a column, not a folder:** each row carries `usage_date_utc` (the UTC day
  the usage happened, the finest time grain), `first_usage_at_utc` /
  `last_usage_at_utc` (the timestamps bounding that day's activity for the row —
  also UTC, per the `_utc` suffix), `period_start` / `period_end`
  (the calendar month it bills to — a monthly rollup is just a `GROUP BY`), and
  `generated_at` (when the snapshot was produced, *not* a usage time). Records
  accumulate across days and months in one table.
- **Where it lands:** `<prefix>/claudeusagesummary.csv` and
  `<prefix>/claudeusagelineitems.csv` in the configured container. Regenerated in
  full each sync and overwritten → Fabric loads-to-table with **overwrite**, always
  the complete de-duplicated history, no partitioning to reconcile.
- **Configurable freshness:** `scheduler.py` regenerates the *current* month
  (month-to-date) and ships it, at the cadence in `SYNC_FREQUENCY`
  (hourly/daily/weekly/monthly, default daily). Run it as a self-pacing loop (the
  `sync` compose service) or fire `deploy/run-sync.sh` from cron
  (`scheduler --emit-cron` prints the line). Late-arriving telemetry is picked up
  by the next run. The billing *period* stays a calendar month — frequency only
  controls how often the snapshot is pushed.
- **Durability:** the outbox lives in `otel.db`, so a crash or a lake/network
  outage never loses an invoice — it stays `pending` and the next run retries.
- **Config:** set the target + auth in `.env` (see `.env.example` — `SYNC_TARGET`,
  `ADLS_*`/`ONELAKE_*`, `AZURE_*`). Unset → invoices are written locally only.
- **Runs where `otel.db` lives** (the receiver host); SQLite is single-host.

---

## Next steps

Rollout plan from the current single-machine setup to company-wide capture.

### The constraint that shapes everything

**SQLite is single-host and single-connection.** `otel_store.py` opens one
`sqlite3.connect(path)` (no WAL, no `check_same_thread=False`) and `receiver.py`
uses `HTTPServer`, not `ThreadingHTTPServer`. Therefore:

- Exactly **one** receiver process, on **one** host, with a **persistent disk**.
  No horizontal scaling, no autoscale, no serverless.
- Requests are handled **serially** — one slow or oversized POST blocks every
  other machine's export.
- The sync job must run **on that same host** (it reads the same `otel.db`).

Everything below either works inside that constraint or states when to break it.

### Where the receiver gets hosted

**A single Azure Linux VM** (B2s / D2s v5 + a managed data disk) in the same
region as `sacyclotroninsights`, running `docker-compose.yml` with the disk
mounted at `./otel-data`. Roughly $40–80/mo.

| Option | Verdict |
|---|---|
| **Azure VM** | ✅ Matches single-host SQLite. Full control of TLS, timers, backups. |
| Container Apps / App Service | ❌ SQLite needs a real filesystem; Azure Files means SQLite locking over SMB (corruption risk). Scale-to-zero drops telemetry. |
| AKS | ❌ Orchestrating a deliberately single-replica stateful pod. |

Sizing is generous at 2 vCPU — per request the work is a JSON parse plus a few
`INSERT OR IGNORE`s. **Disk growth is the thing to watch, not CPU.**

**Network reachability is the harder question.** `managed-settings.json` points at
an internal hostname, but dev laptops roam. Claude Code's OTLP exporter keeps an
**in-memory** queue: if the endpoint is unreachable it retries briefly, then
**drops the datapoints on process exit**. There is no on-disk spool, so off-VPN
sessions are permanently unbilled. Two viable postures:

- **A — internal-only + always-on VPN.** No code changes; accept the coverage
  loss and measure it with `reconcile.py`.
- **B — public HTTPS + shared bearer token** (recommended if devs work off-VPN).
  The receiver already supports this: ship the token via
  `OTEL_EXPORTER_OTLP_HEADERS` + `CLAUDE_BILLING_TOKEN` in managed settings and
  set the same value as `RECEIVER_AUTH_TOKEN` on the host.

> **Auth is implemented but not self-enforcing.** With `RECEIVER_AUTH_TOKEN`
> unset the receiver accepts *anything* that can reach `:4318` — arbitrary
> token/cost rows into billing truth, over plain HTTP. It warns loudly at startup,
> but a warning in a log is not a control. Set the token, then start with
> `--require-auth` so a future misconfiguration can't silently reopen it. The
> token authenticates a *machine*, not a user: it stops outsiders, not a holder
> of the token forging rows.

### Does this consume developer machine capacity?

**No, negligibly.**

- **Network** — one POST per `OTEL_METRIC_EXPORT_INTERVAL` (60s) per active
  session, gzipped JSON of a few KB. Delta temporality means idle minutes emit
  nothing.
- **CPU** — the OTEL SDK batches on a background thread, inside a process already
  waiting on model responses. Unmeasurable against Claude Code's own footprint.
- **`claude-wrapper.sh`** — one `git config --get remote.origin.url` plus an
  `exec`, once per session launch. Not in the per-turn path.
- **Laptop disk** — zero; nothing is spooled locally.

The real developer-facing cost is behavioral, not performance: sessions must
start **inside a git repo with an `origin` remote** (else `repo=unknown`, which is
unattributable), and the **VS Code extension does not export OTEL**, so the CLI is
the only billable surface. Both are coverage problems wearing UX clothing.
Re-confirm the VS Code limitation against the current Claude Code version before
building policy on it.

### Phase 0 — Decisions and clearances (before any infra)

1. **Fleet size + peak concurrent sessions.** The branch point for *Harden*:
   SQLite is fine to a few hundred devs; beyond that, Collector + Postgres.
2. **Network posture** — A or B above.
3. **Employee notice.** The pipeline stores `user_email` per repo per day, and
   managed settings deliberately prevent opting out. That is per-developer
   monitoring; involve HR/legal for notice or works-council consultation as the
   jurisdiction requires. Cheap now, expensive after rollout.
4. **Ownership / on-call.** Receiver downtime is *silently lost revenue*, not a
   visible outage — nobody files a ticket.
5. **Client-mapping owner** — the `repoclientmap` sheet (section 4) is now a
   billing input. Who owns adding a repo when a project starts, and who is
   accountable when one is missing? See Phase 6.1.

### Phase 1 — Stand up the server (1–2 days)

1. Provision the VM; attach and mount a managed disk for `otel-data`.
2. Clone to `/opt/cyclotron/internal-billing-engine` (the path
   `scheduler --emit-cron` already prints).
3. Fill `.env` from `.env.example`: `RECEIVER_AUTH_TOKEN` (`openssl rand -hex 32`),
   `SYNC_TARGET=adls`, `SYNC_AUTH=sp`, the `ADLS_*` values, and the `AZURE_*`
   service principal. Grant that SP **Storage Blob Data Contributor** on the
   container.
4. Enable TLS — uncomment the Caddy sidecar in `docker-compose.yml` and point DNS
   at the VM. Dev machines hit `https://`, never raw `:4318`.
5. `docker compose up -d --build`; verify with `billing.otel.sample_payload`
   against the real endpoint and a `--dry-run` sync.
6. **Prove the full chain with synthetic data before enrolling a single laptop** —
   receiver → `otel.db` → export → ADLS → Fabric table. Debugging this with live
   fleet traffic is miserable.

### Phase 2 — Pilot (1–2 weeks, 5–10 volunteers)

Enrol volunteers with the opt-in package rather than MDM — build it once
(`python3 client-package/build.py --endpoint https://… --token <TOKEN>`) and send
the zip; they double-click `Install`. See `client-package/ADMIN.md` for how to
distribute it, and note the built zip contains a live token.

The pilot exists to produce numbers that cannot be estimated:

- **Coverage** — `python -m billing.reconcile --start … --end …`. The funnel
  (truth → captured → repo-tagged) is the acceptance test. truth→captured is
  telemetry loss (roaming, restarts, VS Code); captured→tagged is the `unknown`
  bucket (sessions outside a git repo).
- **Rows per developer per day** — count `token_usage` rows ÷ active devs. This is
  the input to the capacity plan below.
- **The `unknown` rate** — flagged by `bill.py`. High means a workflow problem to
  fix with policy, not code.

**Do not start the fleet rollout until coverage is a number worth defending to a
client.** Invoices will be built on it.

### Phase 3 — Harden (during the pilot, in this order)

1. ~~**Receiver authentication.**~~ **Done** — `do_POST` validates a shared token
   (constant-time) before reading the body. What's left is *enforcement*: set
   `RECEIVER_AUTH_TOKEN` on the host and restart with `--require-auth`, in the
   order given in `deploy/README.md` so you don't drop telemetry mid-rollout.
   Still open: there's no dual-token window, so rotation needs a quiet period.
2. **Concurrency.** Serial handling queues under fleet load. Moving to
   `ThreadingHTTPServer` *also* requires fixing the store — `check_same_thread=False`
   plus a write lock (or a connection per thread) — and `PRAGMA journal_mode=WAL`
   so the sync job's long reads don't block ingest. **Do not do one without the
   other**; threading the server against today's single-connection store throws
   at runtime.
3. **Log rotation.** `receiver.py:_log()` appends every request to `receiver.log`
   forever with no rotation. On a fleet that fills the disk, and a full disk stops
   ingest.
4. **Backups.** `otel.db` is the sole source of billing truth. Nightly
   `sqlite3 .backup` (**not** a file copy of a live DB) to blob storage, with a
   **tested restore**.
5. **Monitoring.** Alert on: receiver down; zero datapoints in N business hours;
   `fabric_outbox` rows in `failed`; disk >80%. `run-sync.sh` already exits
   non-zero on delivery failure — wire it to alerting.
6. **Capacity checkpoint.** `export.py` rebuilds all history each sync with a full
   `GROUP BY` scan. Correct, simple, and fine for year one; it degrades as raw
   datapoints accumulate. Multiply the pilot's rows/dev/day by fleet size to find
   the date, and plan a compaction step (roll datapoints older than ~90 days into
   daily aggregates, prune) before the DB reaches a few GB.

If Phase 0 says the fleet is large (~500+ devs), skip the SQLite hardening: put an
**OpenTelemetry Collector** in front for buffering/retry and migrate the store to
Postgres.

### Phase 4 — Fleet rollout (waves, 2–4 weeks)

Push via MDM in waves (10% → 50% → 100%), watching receiver load and the
`unknown` rate at each step:

**Three artifacts, not two** — `managed-settings.json`, `claude-wrapper.sh`, and
`claude-repo-tag.py`. The hook is what keeps a mid-session repo switch from
billing to the wrong client, and it is the only one that covers non-CLI surfaces.

1. `managed-settings.json` to the system path for each OS, with the real fleet
   token substituted for the placeholders.
2. Real binary at `/opt/cyclotron/claude-real`, `claude-wrapper.sh` as the only
   `claude` on PATH, and **`CLAUDE_REAL_BIN` pinned** — auto-discovery exists, but
   across Homebrew/npm/nvm installs PATH-order guessing is how you get a wrapper
   that execs itself or a stale binary.
   Pin it **in the shell environment** (system profile or MDM environment
   payload), *not* in `managed-settings.json` — Claude Code applies that file's
   `env` to itself, and the wrapper is its parent process, so it never sees it.
   Ship **one copy** of the wrapper per machine (symlinks are fine); two copies
   used to exec each other and hang the session. See `deploy/README.md`.
3. `claude-repo-tag.py` to `/opt/cyclotron/claude-repo-tag.py`, executable, with
   the path matching what `managed-settings.json` registers. Needs `python3` on
   the PATH.
4. **All three together.** Settings without the wrapper is the worst outcome: it
   looks like success while being entirely unbillable. Wrapper without the hook
   bills mid-session repo switches to the wrong client — which is worse than not
   billing, because it's wrong rather than missing.
5. Verify per wave using `deploy/README.md`, and confirm the managed-settings path
   against the installed Claude Code version rather than trusting the table.
   `bill.py`'s ATTRIBUTION SOURCE breakdown is the fastest read on whether the
   hook actually landed: all-`wrapper` means it didn't.

### Phase 5 — Schedule the sync

**Use a systemd timer — not the `--loop` service, and not plain cron.**

The `sync` compose service calls `time.sleep(86_400)`; with
`restart: unless-stopped`, every reboot resets the phase, so "daily 23:30" drifts
to whenever the box last came up. A systemd timer with `Persistent=true` also
catches runs missed while the VM was down, and gives `journalctl` history and
exit-code handling for free.

```
# scheduler --emit-cron prints the equivalent line
30 23 * * *  cd /opt/cyclotron/internal-billing-engine && ./deploy/run-sync.sh
```

**Drop the `sync` service from `docker compose up`** so two schedulers aren't
racing on the same DB.

**Then sequence Fabric carefully.** `fabric_client.upload_file` is
create-truncate → append → flush, so between truncate and flush the blob is empty
or partial. `refresh_billing_tables.py` overwrites the Delta tables from those
CSVs — firing inside that window overwrites a good table with a truncated one.
Cheapest fix first:

- Schedule the notebook with a wide margin (sync 23:30 UTC → notebook 00:30 UTC)
  **and** have it sanity-check row count + `generated_at` before overwriting.
- Better: upload to `<name>.tmp` and copy to the final path on success, so readers
  only ever see complete files.

Start at `SYNC_FREQUENCY=daily`. `hourly` re-uploads the entire history every hour
and widens the truncate window twenty-four-fold.

### Phase 6 — Harden the client-invoice layer

Client mapping and aggregation now live in Fabric (section 4): the notebook joins
`claudeusagelineitems` to `repoclientmap` and groups by client/repo/model. That
closes the "can we invoice at all" gap. What's left is making it *trustworthy*
enough to bill on — note the billing identity now spans two systems, so the Python
engine and the Fabric notebook can disagree.

1. **Decide what happens to an unmapped repo.** The single highest-risk item. If
   the notebook **inner**-joins `repoclientmap`, every repo missing from the sheet
   is silently dropped — real usage, invisibly unbilled, with no error anywhere.
   Left-join instead and surface unmapped repos as an explicit exception row that
   someone has to clear before the invoice is issued. **Never default an unmapped
   repo to a client.**
2. **Fix the refresh ordering.** The dataflow refreshes `repoclientmap` daily at
   **23:59 PST**, but the sync ships CSVs at **23:30 UTC** (= 16:30 PST) and the
   Delta-table notebook runs shortly after. So the invoice notebook can join
   against a map that is up to a day stale — a repo mapped today won't bill
   correctly until tomorrow. Either move the dataflow ahead of the notebook or
   have the notebook trigger/verify the refresh before joining. Also note this is
   the one **PST** schedule in an otherwise all-UTC pipeline; DST shifts it twice
   a year relative to everything else.
3. **Make the mapping auditable.** A SharePoint workbook has no meaningful version
   history for billing purposes — you cannot reconstruct which mapping produced
   last quarter's invoice, which is exactly what a client dispute asks for.
   Snapshot the resolved map alongside each invoice, or move it to a Fabric table
   with effective-dated rows (`valid_from` / `valid_to`) so re-running a closed
   month reproduces the original numbers. Also add validation on ingest: one
   client per repo, no blank clients, no duplicate repo keys.
4. **Reconcile Fabric totals against `otel.db`.** Two aggregation paths now exist
   (`invoice.py` locally, the notebook in Fabric). Assert per-month totals match;
   a silent divergence means one of them is wrong and you won't know which.
5. **Real rates.** `rating.py` rates are placeholders. Billing runs off actual
   reported cost so this mainly affects the cross-check — but a cross-check
   against fictional numbers isn't one.
6. **Confirm the markup.** `1.5` is hardcoded as the default in `scheduler.py` and
   `run-sync.sh`, and is applied *before* Fabric sees the data. If markup varies by
   contract it belongs next to the client mapping, not in a CLI default — and it
   must be applied in exactly one place.
7. **Fix the invoice period label** — `invoice.py` prints
   `Period: 2026-07-01 → 2026-08-01`, but `period_end` is *exclusive*. A client
   will read it as inclusive and ask why they're billed for August 1. Display
   `→ 2026-07-31` (or `July 2026`) while keeping the exclusive bound in the query.
   The section-4 notebook filters on the same columns, so apply the same
   convention there. Same clarification belongs in `fabric/README.md`, where the
   column is described without noting the bound is exclusive.
8. **A sign-off step.** Invoices persist as `status='draft'`. Someone must review
   and flip to `issued` before money is requested. Never auto-send.

### Phase 7 — Steady state

Monthly close (`scheduler --month YYYY-MM` backfills a closed month once late
telemetry settles), reconcile-based coverage as an ongoing KPI, onboarding for new
repos and new hires, and a quarterly review of the `unknown` bucket.

### Open decisions

Two answers change the plan materially:

- **Fleet size + peak concurrent sessions** — decides SQLite-plus-hardening vs.
  Collector-plus-Postgres, a very different Phase 3.
- **Do developers work off-VPN?** — decides whether the endpoint is internal-only
  or public HTTPS, and how much coverage loss the pilot should be expected to
  reveal. Auth exists either way; a public endpoint makes enforcing it
  (`--require-auth`) and TLS non-negotiable rather than merely advisable.
- **Enforced or opt-in?** — MDM (`deploy/`) removes the coverage question but
  needs the HR/legal notice in Phase 0.3 first; the opt-in package
  (`client-package/`) ships today but leaves coverage voluntary, so
  `reconcile.py` becomes the number that matters.
