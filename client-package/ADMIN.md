# Distributing this package — read before sending it to anyone

Audience: whoever owns the billing rollout. `INSTRUCTIONS.md` is the developer-facing
file; this one is for you.

## What's in the package

| File | Purpose |
|---|---|
| `INSTRUCTIONS.md` | Developer-facing setup, what's collected, uninstall |
| `install.sh` | macOS shim — finds `python3`, hands off to `configure.py` |
| `install.ps1` | Windows shim — finds a real `python.exe`, hands off to `configure.py` |
| `configure.py` | The actual install/uninstall/verify logic, shared by both platforms |
| `claude-repo-tag.py` | The repo-tag hook that gets installed |
| `VERSION` | Package version — bump when you change anything here |

The settings merge lives in `configure.py` only, so the two platforms cannot drift
apart. If you add a platform, write another shim; don't reimplement the merge.

## Sending it out

1. **Never put the token in the package**, in chat, or in a ticket. Send it over
   1Password separately from the zip. `configure.py` rejects `PASTE_…` /
   `REPLACE_…` placeholders and anything under 16 characters.
2. **Give people an `https://` endpoint.** `configure.py` refuses a plaintext
   `http://` URL unless `--allow-insecure` is passed, because every export carries
   a developer's work email, repo names and cost — plus the shared token — and the
   token is readable on every machine that has it. Anyone who captures it can
   write arbitrary rows into billing truth.
3. **Tell people what it collects before they install it**, not after. The
   developer-facing list is at the top of `INSTRUCTIONS.md`.

## Verifying a machine

The installer self-verifies, and anyone can re-check later:

```bash
bash install.sh verify --endpoint https://receiver…      # macOS
.\install.ps1 -Verify -Endpoint https://receiver…        # Windows
```

Verification POSTs an empty body to `/v1/ping`. The receiver checks auth before
routing the path and acknowledges unknown paths without storing anything
(`billing/otel/receiver.py:243`, `:280`), so this proves reachability and auth
while writing **nothing** into `otel.db`.

One caveat worth knowing: a receiver running **without** `RECEIVER_AUTH_TOKEN`
accepts any token, so a green verify against an open receiver does not prove the
token is correct. Enforce auth on the receiver (`--require-auth`) before you rely
on that signal.

## Blockers to clear before a company-wide push

These are from this repo's own `README.md`, and they are still true in the code.
The package is ready; the pipeline behind it is not.

1. **The receiver handles requests serially.** `receiver.py:290` uses `HTTPServer`,
   not `ThreadingHTTPServer`, and `otel_store.py:146` opens a single
   `sqlite3.connect(path)` with no WAL. One slow or oversized POST blocks every
   other machine's export. README Phase 3.2 is explicit that fixing the server
   without also fixing the store **throws at runtime** — do both or neither.
2. **`receiver.log` never rotates.** `receiver.py:_log()` appends every request
   forever. On a fleet that fills the disk, and a full disk stops ingest.
3. **No backups.** `otel.db` is the sole source of billing truth. README Phase 3.4
   wants a nightly `sqlite3 .backup` (not a file copy of a live DB) with a *tested*
   restore.
4. **No TLS on the pilot endpoint.** README Phase 1.4 wants the Caddy sidecar in
   `docker-compose.yml` enabled and DNS pointed at the VM.
5. **Employee notice.** README Phase 0.3: the pipeline stores `user_email` per repo
   per day. That is per-developer monitoring; HR/legal notice or works-council
   consultation may be required depending on jurisdiction. "Cheap now, expensive
   after rollout."
6. **Coverage numbers from the pilot.** README Phase 2: *"Do not start the fleet
   rollout until coverage is a number worth defending to a client."* Run
   `python -m billing.reconcile --start … --end …` and look at the
   truth → captured → tagged funnel before widening.

Roll out in waves (10% → 50% → 100%), watching receiver load and the `unknown`
rate at each step.

## Known gaps in this package

- **Linux has no shim.** Only macOS and Windows were requested. `configure.py` is
  platform-neutral and works on Linux; it just needs an `install.sh` equivalent
  (or reuse `install.sh` — it is plain bash and should work unmodified).
- **This is the opt-in track only.** The MDM/enforced path is
  `deploy/managed-settings.json` plus `deploy/claude-wrapper.sh`, and per README
  Phase 4.3 those two must ship *together* — settings without the wrapper looks
  like success while being entirely unbillable.
- **The hook records the interpreter path found at install time.** If a developer
  later removes or relocates that Python, the hook stops firing silently. Re-running
  the installer fixes it. A wrapper script indirection would be more robust.
- **No signing or integrity check.** Anyone who can modify the zip in transit can
  change the endpoint or the hook. If you distribute it outside a trusted channel,
  publish a checksum.

## Fixed relative to `pilot-package/`

- **Merges instead of refusing.** The old `install.sh` bailed out when
  `~/.claude/settings.json` already existed and told the developer to hand-merge
  JSON. Most developers have that file, so at company scale that is a support
  ticket per developer.
- **Windows works at all.** The old package was bash-only. It also relied on
  `chmod +x` and the shebang, which do nothing on Windows, and on invoking a bare
  `.py` path, which needs a file association that a `--scope user` Python install
  does not create. The hook command now names the interpreter explicitly.
- **Dropped `OTEL_LOGS_EXPORTER=none`.** `pilot-package/settings.json` set it;
  `deploy/README.md` warns that Claude Code's logs exporter only accepts `otlp`
  and `console`, so `none` is unrecognised and makes it **error on startup and
  exit**. The key is now omitted, and `configure.py` deletes it from machines that
  ran the pilot.
- **Idempotent, backed up, and reversible.** Re-running replaces its own entries
  instead of stacking duplicates; a timestamped backup is written before any
  change; `uninstall` removes exactly the keys and hook entries it owns and leaves
  everything else alone.
- **Atomic writes.** Settings are written to a temp file and `os.replace`d, so an
  interrupted install cannot leave a truncated `settings.json` — which would break
  Claude Code on that machine.
- **Self-verifying.** The installer tells the developer immediately if the token is
  rejected or the receiver is unreachable, instead of failing silently and
  producing no billing data.
