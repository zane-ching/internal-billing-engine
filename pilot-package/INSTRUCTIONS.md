# Claude Code usage-billing pilot — setup (opt-in)

- points your Claude Code session at an internal receiver so your usage can be attributed to the repo
- opt-in and fully reversible — everything installs into your home directory, nothing is enforced, and uninstalling is deleting two files

## What's collected

Per Claude Code session the receiver records:

- the **git remote** of the repo you're working in (e.g. `github.com/zane-ching/foo`)
- the **model**, **token counts**, and **cost** of the usage
- your **work email**, a **session id**, and **timestamps**

It does **not** collect: your prompts, your code, file contents, or file paths — none
of that is in Claude Code's telemetry, and this pilot ships metrics only (logs are
turned off)

## Prerequisites

- **`python3`** on your PATH (`python3 --version`) — the repo-tag hook needs it.
- I will send a **shared token** over 1Password

## Install

From inside this folder, in a terminal:

```
bash install.sh <PASTE_THE_TOKEN_HERE>
```

That copies the hook to `~/.cyclotron/claude-repo-tag.py` and writes
`~/.claude/settings.json`. If you already have a `~/.claude/settings.json`, it
won't touch it — see "Manual install" below.

## Install — manual (or if you already have a ~/.claude/settings.json)

1. Copy the hook somewhere stable and make it executable:
   ```
   mkdir -p ~/.cyclotron
   cp claude-repo-tag.py ~/.cyclotron/claude-repo-tag.py
   chmod +x ~/.cyclotron/claude-repo-tag.py
   ```
2. Open `settings.json` from this folder. In it:
   - replace **both** `PASTE_SHARED_TOKEN_HERE` with the token from Zane
   - replace **every** `REPLACE_WITH_HOOK_PATH` with the full path to the hook,
     which is your home directory + `/.cyclotron/claude-repo-tag.py`
     (run `echo $HOME/.cyclotron/claude-repo-tag.py` to get the exact string)
3. If you have **no** `~/.claude/settings.json`, save the edited file there.
   If you **already have one**, merge the `env` and `hooks` blocks into it instead
   of overwriting — keep your existing keys.

## Uninstall (any time)

```
rm -f ~/.cyclotron/claude-repo-tag.py
```
Then open `~/.claude/settings.json` and remove the `env` and `hooks` entries you
added (or delete the file if you created it only for this). That's it — fully removed.
