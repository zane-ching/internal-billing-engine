# End-to-end demo runbook

**The story:** a real Claude Code session → tagged with the git repo → captured →
turned into a per-repo invoice. You are the only "employee"; usage bills to the
repo it was done in, **internal-billing-engine**.

You use **Claude Code in the VS Code extension** (the one you already work in) plus
**one terminal** for the receiver. In the project directory:
```
cd /Users/zaneching/Desktop/internal-billing-engine
```
> No Anthropic API token is needed — OTEL telemetry is local. The extension emits
> telemetry directly (verified: token + cost metrics, tagged with the repo).

---

## Before the boss arrives (30 seconds)

**Optional clean slate** — start empty so the invoice shows only today's usage.
Do this *before* starting the receiver:
```
rm -f data/otel.db
```

**Start the receiver in a terminal, leave it running:**
```
python3 -m billing.otel.receiver
```
It prints `[receiver] listening on http://127.0.0.1:4318`. Keep this window
visible next to VS Code — it prints a line every time telemetry arrives.
**Don't close it.**

> If you didn't just reload VS Code, do it once so the extension is definitely
> reading the telemetry config: `Cmd+Shift+P` → **Developer: Reload Window**.

---

## Part 1 — "Here's how telemetry is turned on"

Open and show:

- **`.claude/settings.local.json`** — the `env` block that turns telemetry on for
  Claude Code on this machine and points it at the receiver, and tags sessions
  with this repo:
  ```json
  "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
  "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318",
  "OTEL_RESOURCE_ATTRIBUTES": "repo=https://github.com/zane-ching/internal-billing-engine.git"
  ```
  The VS Code extension reads this and emits `claude_code.token.usage` +
  `claude_code.cost.usage`.
- **`deploy/managed-settings.json`** — the same config, in the form IT pushes to
  every developer machine via **MDM**. Because it lives at the system settings
  path, developers **can't turn it off**.

One line to say: *"On my machine this is a local settings file; in production IT
pushes the same thing fleet-wide and enforces it."*

---

## Part 2 — "Now I use Claude Code and spend tokens"

Just **use Claude Code normally in VS Code** — this very extension. Ask it a few
real things so it spends tokens, e.g.:
- `summarize what this billing engine does`
- `list the files in billing/otel and what each does`

**Watch the receiver terminal** — within a few seconds to a minute you'll see:
```
[receiver] /v1/metrics tok+=4 cost+=1 dup=0 metrics_seen=['claude_code.cost.usage', 'claude_code.token.usage']
```
That's your extension's usage being captured live, tagged with the repo.

**Show the captured, repo-tagged records:**
```
python3 -m billing.otel.records --limit 3
```

---

## Part 3 — "And here's the invoice"

```
# (optional) preview the bill on actual Anthropic cost — no mapping step needed;
# usage bills to the repo it was done in (here: internal-billing-engine)
python3 -m billing.otel.bill

# generate the invoice for the period
python3 -m billing.otel.invoice --start 2026-07-01 --end 2026-08-01 --markup 1.5

# show the invoice document
cat invoices/2026-07-01_2026-08-01/INV-2026-07-01-internal-billing-engine.txt
```
Also open `invoices/2026-07-01_2026-08-01/summary.csv` and `line_items.csv` in
the editor — those are what a finance system would ingest.

---

## What each part proves (talking points)

1. **Configuration** — telemetry is turned on (and in production, enforced
   centrally); each session is tagged with the repo it ran in.
2. **Capture** — real usage (tokens *and* Anthropic's actual dollars) flows to
   our receiver, attributed to the repo, deduplicated.
3. **Attribution** — usage bills to the repo it was done in (here
   `internal-billing-engine`); no naming rules to configure. An optional
   override map can rename or group repos if finance wants that.
4. **Invoice** — a persisted, finance-ready per-repo bill (actual cost ×
   markup), with `unknown` usage (no git remote) flagged rather than silently
   dropped.

Scaling to the whole company changes nothing in this flow — more developers just
means more repos, each billing under its own name.

---

## If something looks off (quick fixes)

- **No `[receiver]` lines appear** → (a) the receiver isn't running — restart it;
  (b) the extension hasn't picked up the config — `Cmd+Shift+P` →
  **Developer: Reload Window**, then use Claude Code again. Exports arrive on a
  timer (up to ~1 min), so give it a moment.
- **`connection refused`** → the receiver isn't running; start it (see "Before").
- **Invoice shows `$0` on a model line** → expected for tiny Haiku usage; billing
  is on total actual cost, so the client total is still correct.

---

## What's NOT in this demo (say it upfront so there are no surprises)

- The receiver here runs locally; production hosts it behind TLS
  (`Dockerfile` / `docker-compose.yml` are ready for that).
- Rollout to all machines needs **MDM** to push + enforce the settings (the open
  question for IT). At fleet scale the repo tag is set per repo (a committed
  `.claude/settings.json`, or the equivalent), while `managed-settings.json`
  handles the global "telemetry on + endpoint".
- Invoices are `draft`; a production step would freeze + assign permanent numbers.

> CLI alternative: `deploy/dev-selftest.sh` + `deploy/claude-wrapper.sh` do the
> same for the `claude` CLI (the wrapper computes the repo tag dynamically). Not
> needed for this demo — the extension is the simpler path.
