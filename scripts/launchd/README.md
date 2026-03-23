# Automating `re-ass` with `launchd`

This guide explains how to run `re-ass` automatically on macOS using the LaunchAgent files in this directory.

## What is included

- `com.user.re-ass.plist.template`: a LaunchAgent template
- `render-plist.sh`: fills in the correct local paths for `uv`, the repo root, and the log directory

The rendered plist uses a default schedule of `7:00 AM` every day. If that works for you, you can keep it. If you want a different schedule, edit the rendered plist before installing it.

## Before you automate

Make sure these steps work first:

1. Run setup:

   ```bash
   REPO_ROOT="/path/to/research-assistant"
   cd "$REPO_ROOT"
   ./scripts/setup.sh
   ```

2. Configure your provider, settings, and preferences:

   - `user_preferences/settings.toml`
   - `user_preferences/preferences.md`

3. Run a manual test:

   ```bash
   uv run re-ass
   ```

Automation should only be installed after a manual run succeeds. This is especially important for CLI-backed providers such as Claude, Codex, Copilot, or Gemini, because they must already be authenticated for non-interactive use.

## Render the plist

From the repo root:

```bash
./scripts/launchd/render-plist.sh
```

This writes the rendered plist to:

```text
tmp/launchd/com.user.re-ass.plist
```

The rendered plist contains absolute paths for `uv`, the repo root, and the log directory. If you move this repo, reinstall `uv` somewhere else, or want to pick up a different `uv` binary, rerun `./scripts/launchd/render-plist.sh` and reinstall the LaunchAgent.

## Optional: customise the schedule

If you want anything other than the default daily `7:00 AM` schedule, edit the rendered plist before installing it.

Open the rendered plist and edit the `StartCalendarInterval` section:

The default template looks like this:

```xml
<key>StartCalendarInterval</key>
<dict>
    <key>Hour</key>
    <integer>7</integer>
    <key>Minute</key>
    <integer>0</integer>
</dict>
```

### Example: Monday to Friday at 1:00 PM

Replace that block with:

```xml
<key>StartCalendarInterval</key>
<array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
</array>
```

`Hour` uses 24-hour format (0–23), so 1:00 PM = `13`, 9:00 AM = `9`, midnight = `0`.

`launchd` weekday numbers are:

- `1`: Monday
- `2`: Tuesday
- `3`: Wednesday
- `4`: Thursday
- `5`: Friday
- `6`: Saturday
- `0` or `7`: Sunday

## Install the LaunchAgent

If you kept the default schedule, install the rendered plist as-is. If you changed the schedule, install your edited version.

```bash
REPO_ROOT="/path/to/research-assistant"
mkdir -p ~/Library/LaunchAgents
cp "$REPO_ROOT/tmp/launchd/com.user.re-ass.plist" \
  ~/Library/LaunchAgents/com.user.re-ass.plist
plutil -lint ~/Library/LaunchAgents/com.user.re-ass.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.user.re-ass.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.re-ass.plist
```

## Test the installed job

Run the job immediately:

```bash
launchctl kickstart -k gui/$(id -u)/com.user.re-ass
```

Then inspect the logs:

```bash
REPO_ROOT="/path/to/research-assistant"
tail -n 50 "$REPO_ROOT/logs/last-run.log"
tail -n 50 "$REPO_ROOT/logs/launchd.stdout.log"
tail -n 50 "$REPO_ROOT/logs/launchd.stderr.log"
```

`re-ass` also writes run diagnostics under:

```text
state/runs/
state/papers/
```

## Operational notes

- `launchd` uses your Mac's local timezone.
- If your Mac is asleep when a run is due, `launchd` coalesces missed calendar events and runs the job after wake.
- `re-ass` tracks prior successful runs, so scheduled runs continue from the previous successful interval rather than reprocessing the whole history.

## Updating or removing the job

If you change the installed plist, reload it:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.user.re-ass.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.re-ass.plist
```

To remove the automation entirely:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.user.re-ass.plist
rm ~/Library/LaunchAgents/com.user.re-ass.plist
```
