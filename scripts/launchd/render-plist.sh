#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
template="$script_dir/com.user.re-ass.plist.template"
tmp_dir="$repo_root/logs/launchd"
log_dir="$repo_root/logs"
output="$tmp_dir/com.user.re-ass.plist"

uv_bin="$(command -v uv || true)"
if [[ -z "$uv_bin" ]]; then
  printf 'uv is required but was not found on PATH.\n' >&2
  exit 1
fi

path_env=""

append_path_entry() {
  local entry="$1"
  if [[ -z "$entry" ]]; then
    return
  fi
  case ":$path_env:" in
    *":$entry:"*) return ;;
  esac
  if [[ -z "$path_env" ]]; then
    path_env="$entry"
  else
    path_env="$path_env:$entry"
  fi
}

append_path_entry "$(dirname "$uv_bin")"

if [[ -n "${PATH:-}" ]]; then
  IFS=':' read -r -a current_path_parts <<< "$PATH"
  for entry in "${current_path_parts[@]}"; do
    append_path_entry "$entry"
  done
fi

for entry in /opt/homebrew/bin /usr/local/bin /usr/bin /bin /usr/sbin /sbin; do
  append_path_entry "$entry"
done

mkdir -p "$tmp_dir" "$log_dir"

escape_for_sed() {
  printf '%s' "$1" | sed 's/[\/&]/\\&/g'
}

escaped_uv_bin="$(escape_for_sed "$uv_bin")"
escaped_repo_root="$(escape_for_sed "$repo_root")"
escaped_log_dir="$(escape_for_sed "$log_dir")"
escaped_path_env="$(escape_for_sed "$path_env")"

sed \
  -e "s/__UV_BIN__/$escaped_uv_bin/g" \
  -e "s/__REPO_ROOT__/$escaped_repo_root/g" \
  -e "s/__LOG_DIR__/$escaped_log_dir/g" \
  -e "s/__PATH__/$escaped_path_env/g" \
  "$template" > "$output"

printf 'Rendered launchd plist for re-ass.\n'
printf 'Template: %s\n' "$template"
printf 'Output:   %s\n' "$output"
printf 'Repo:     %s\n' "$repo_root"
printf 'uv:       %s\n' "$uv_bin"
printf 'Logs:     %s\n' "$log_dir"
printf '\n'
printf 'Embedded PATH:\n'
printf '%s\n' "$path_env"
printf '\n'
printf 'Next steps:\n'
printf '1. If needed, edit StartCalendarInterval in %s\n' "$output"
printf '2. From the repo root, run ./scripts/launchd/install-plist.sh\n'
printf '3. Optional: to trigger a real run immediately and pull the latest available papers, run launchctl kickstart -k gui/$(id -u)/com.user.re-ass\n'
