#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"

default_source="$repo_root/tmp/launchd/com.user.re-ass.plist"
source_plist="${1:-$default_source}"
launch_agents_dir="$HOME/Library/LaunchAgents"
installed_plist="$launch_agents_dir/com.user.re-ass.plist"
label="com.user.re-ass"
domain="gui/$(id -u)"
service_target="$domain/$label"

resolve_path() {
  local target="$1"
  local target_dir
  target_dir="$(cd -- "$(dirname -- "$target")" && pwd -P)"
  printf '%s/%s\n' "$target_dir" "$(basename -- "$target")"
}

if [[ ! -f "$source_plist" ]]; then
  printf 'Rendered plist not found: %s\n' "$source_plist" >&2
  printf 'From the repo root, run ./scripts/launchd/render-plist.sh first.\n' >&2
  exit 1
fi

mkdir -p "$launch_agents_dir"

resolved_source="$(resolve_path "$source_plist")"
resolved_installed="$(resolve_path "$installed_plist")"

if [[ "$resolved_source" != "$resolved_installed" ]]; then
  cp "$source_plist" "$installed_plist"
fi

plutil -lint "$installed_plist"

launchctl bootout "$domain" "$installed_plist" 2>/dev/null || true
launchctl bootstrap "$domain" "$installed_plist"

printf 'Installed LaunchAgent: %s\n' "$installed_plist"
printf 'Service: %s\n' "$service_target"
printf '\n'
printf 'The LaunchAgent has been reloaded but not kicked off immediately.\n'
printf 'To test it now, run:\n'
printf 'launchctl kickstart -k %s\n' "$service_target"
printf 'This triggers a real re-ass run and may pull the latest available papers immediately.\n'
printf 'Then check the logs dir (e.g. tail -n 50 logs/last-run.log)\n'
