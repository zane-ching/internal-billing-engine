# Claude Code usage billing — setup

This points your Claude Code sessions at an internal receiver so usage can be
attributed to the repo you were working in.

**Opt-in and reversible.** Everything installs under your home directory, nothing
is enforced, nothing needs administrator rights, and uninstall is one double-click.

## Install

Unzip the folder somewhere, then double-click one file:

| Your machine | Double-click |
|---|---|
| **macOS** | `Install.command` |
| **Windows** | `Install.bat` |

A window opens, shows you what is collected, and waits. Press **Enter** to go
ahead, or type `n` and press Enter to back out — nothing is written until you do.
The receiver URL and token are already inside the package; there is nothing to
paste and no terminal to open.

When it finishes it tells you whether the receiver actually accepted you. If that
check fails, **none of your usage is being recorded** — send someone the message
it printed rather than assuming it worked.

### First-run security prompts

Both operating systems distrust a script that arrived in a downloaded zip. This
is expected, and it happens once:

- **macOS** — *"cannot be opened because it is from an unidentified developer."*
  Right-click (or Control-click) `Install.command` → **Open** → **Open** again.
  That approves this one file; it does not change any system setting.
- **Windows** — a blue *"Windows protected your PC"* box. Click **More info** →
  **Run anyway**. If you would rather clear it first: right-click `Install.bat`
  → **Properties** → tick **Unblock** → **OK**.

### You need Python 3.8+

The repo-tag hook is a Python script, so one has to be on the machine.

- **macOS** — usually already there. Check with `python3 --version`. If missing:
  `xcode-select --install`
- **Windows** — check with `python --version`. If it prints nothing useful or
  opens the Microsoft Store, install a real one (no admin needed):
  `winget install --id Python.Python.3.12 --scope user`

  > The `python.exe` in `AppData\Local\Microsoft\WindowsApps` is a Store alias
  > stub, not a working Python. The installer detects and rejects it.

The installer stops with these instructions if it cannot find a usable Python.

## What is collected

Per Claude Code session, the receiver records:

- the **git remote** of the repo you are working in (e.g. `github.com/cyclotron-azure/acme-web`)
- the **model**, **token counts**, and **cost** of the usage
- your **work email**, a **session id**, and **timestamps**

It does **not** collect your prompts, your code, file contents, or file paths.
None of that is in Claude Code's telemetry, and this ships metrics only — event
logs are not exported.

Your usage is visible to whoever administers billing. If that matters to you,
raise it before installing rather than after.

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

To re-check your setup at any time, double-click **`Verify.command`** (macOS) or
**`Verify.bat`** (Windows). It changes nothing and takes a second — worth running
if your usage stops showing up.

## Uninstall

Double-click **`Uninstall.command`** (macOS) or **`Uninstall.bat`** (Windows).

That removes the hook file and only the settings this installer added — your own
settings stay. Telemetry stops with your next session.

## Keep this folder

The Verify and Uninstall launchers live in it. If you delete it, you can always
get another copy from whoever sent you this one.

## Running it from a terminal instead

The launchers are wrappers. If you prefer a shell, or you are scripting a fleet,
the same three operations are available directly and take explicit arguments
(which override whatever the package was built with):

```bash
# macOS
bash install.sh                                    # uses the packaged settings
bash install.sh --token <TOKEN> --endpoint <URL>   # or supply your own
bash install.sh verify
bash install.sh uninstall
```
```powershell
# Windows
.\install.ps1
.\install.ps1 -Token <TOKEN> -Endpoint <URL>
.\install.ps1 -Verify
.\install.ps1 -Uninstall
```

Add `--dry-run` / `-DryRun` to either installer to see what would change without
touching anything. If PowerShell refuses to run the script, allow it for that one
command: `powershell -ExecutionPolicy Bypass -File .\install.ps1`

## Troubleshooting

| Symptom | Cause |
|---|---|
| The window flashes and disappears | The launcher was run from somewhere odd, or the folder is incomplete. Re-extract the whole zip and double-click again. |
| macOS: "unidentified developer" | Expected on a downloaded zip. Right-click → Open → Open. |
| Windows: "Windows protected your PC" | Expected on a downloaded zip. More info → Run anyway. |
| `receiver REJECTED the token (401)` | The package is stale or the token was rotated. Ask for a current package — until it is fixed, none of your usage is recorded. |
| `cannot reach <url>` | Off VPN, or the receiver is down. Claude Code buffers telemetry **in memory only** and drops it when the process exits, so sessions run while the receiver is unreachable are never billed. |
| Installer says Python is a Store alias stub | Install real Python: `winget install --id Python.Python.3.12 --scope user` |
| `No token` / `No receiver endpoint` | This copy was built without the settings baked in. Ask for the one-click package, or pass `--token`/`--endpoint` yourself. |
| Everything installed but usage shows as `unknown` | Sessions were started outside a git repo, or in one with no `origin` remote. |
| It worked, then stopped | The Python it was installed against was moved or removed. Double-click the Install launcher again to re-point it. |
