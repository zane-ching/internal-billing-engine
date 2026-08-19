# Claude Code usage billing — setup

This points your Claude Code sessions at an internal receiver so usage can be
attributed to the repo you were working in.

**Opt-in and reversible.** Everything installs under your home directory, nothing
is enforced, nothing needs administrator rights, and uninstall is one command.

## What is collected

Per Claude Code session, the receiver records:

- the **git remote** of the repo you are working in (e.g. `github.com/cyclotron/acme-web`)
- the **model**, **token counts**, and **cost** of the usage
- your **work email**, a **session id**, and **timestamps**

It does **not** collect your prompts, your code, file contents, or file paths.
None of that is in Claude Code's telemetry, and this ships metrics only — event
logs are not exported.

Your usage is visible to whoever administers billing. If that matters to you,
raise it before installing rather than after.

## Before you start

You need two things from the billing owner:

1. the **receiver URL** (an `https://…` address)
2. the **shared token** — sent over 1Password, never in this folder or in chat

You also need **Python 3.8+**. The repo-tag hook is a Python script.

- **macOS** — usually present. Check with `python3 --version`. If missing:
  `xcode-select --install`
- **Windows** — check with `python --version`. If it prints nothing useful or
  opens the Microsoft Store, install a real one (no admin needed):
  `winget install --id Python.Python.3.12 --scope user`

  > The `python.exe` in `AppData\Local\Microsoft\WindowsApps` is a Store alias
  > stub, not a working Python. The installer detects and rejects it.

## Install — macOS

```bash
bash install.sh --token <TOKEN> --endpoint <RECEIVER_URL>
```

## Install — Windows (PowerShell)

```powershell
.\install.ps1 -Token <TOKEN> -Endpoint <RECEIVER_URL>
```

If PowerShell refuses to run the script, allow it for that one command:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Token <TOKEN> -Endpoint <RECEIVER_URL>
```

### What the installer does

1. copies the repo-tag hook to `~/.cyclotron/claude-repo-tag.py`
2. **merges** the telemetry settings into your `~/.claude/settings.json`, keeping
   everything already in it — your permissions, theme, and any other hooks are
   preserved, and a timestamped `.bak-…` backup is written first
3. checks the receiver is reachable and accepts the token, and tells you if not

Re-running it is safe: it replaces its own entries instead of stacking duplicates.

## After installing

Telemetry starts with your **next** Claude Code session.

Two things determine whether your usage can be attributed:

- **Start sessions inside a git repo that has an `origin` remote.** Without one,
  usage is recorded as `unknown` and cannot be attributed to any project.
- **Use the `claude` CLI.** The VS Code extension does not export telemetry, so
  work done there is not captured.

To re-check your setup at any time:

```bash
# macOS
bash install.sh verify --endpoint <RECEIVER_URL>
```
```powershell
# Windows
.\install.ps1 -Verify -Endpoint <RECEIVER_URL>
```

## Uninstall

```bash
# macOS
bash install.sh uninstall
```
```powershell
# Windows
.\install.ps1 -Uninstall
```

That removes the hook file and only the settings this installer added — your own
settings stay. Telemetry stops with your next session. Add `-DryRun` / `--dry-run`
first if you want to see what would change.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `receiver REJECTED the token (401)` | Wrong or stale token. Get the current one from the billing owner — until it is fixed, none of your usage is recorded. |
| `cannot reach <url>` | Off VPN, or the receiver is down. Claude Code buffers telemetry **in memory only** and drops it when the process exits, so sessions run while the receiver is unreachable are never billed. |
| Installer says Python is a Store alias stub | Install real Python: `winget install --id Python.Python.3.12 --scope user` |
| Everything installed but usage shows as `unknown` | Sessions were started outside a git repo, or in one with no `origin` remote. |
