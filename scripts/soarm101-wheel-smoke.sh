#!/usr/bin/env bash
set -euo pipefail

forge_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sdk_root="$(cd "$forge_root/../soarm101-motion-sdk" && pwd)"
work_dir="$(mktemp -d -t soarm101-wheel-smoke.XXXXXX)"
trap 'rm -rf "$work_dir"' EXIT

wheelhouse_input="${1:-}"
if [[ $# -gt 1 ]]; then
  echo "usage: $0 [complete-wheelhouse-directory]" >&2
  exit 2
fi
command -v uv >/dev/null || { echo "uv is required" >&2; exit 2; }
command -v python3.12 >/dev/null || { echo "Python 3.12 is required" >&2; exit 2; }

mkdir -p "$work_dir/source/forge" "$work_dir/source/sdk" "$work_dir/dist" "$work_dir/wheelhouse"
for root in "$forge_root" "$sdk_root"; do
  [[ -f "$root/pyproject.toml" ]] || { echo "source tree missing pyproject.toml: $root" >&2; exit 2; }
done

# Build from copied working-tree snapshots, including tracked edits and untracked
# package files, so neither wheel nor runtime process imports a sibling checkout.
cp "$forge_root/pyproject.toml" "$forge_root/README.md" "$work_dir/source/forge/"
cp -a "$forge_root/src" "$work_dir/source/forge/src"
cp "$sdk_root/pyproject.toml" "$sdk_root/README.md" "$work_dir/source/sdk/"
cp -a "$sdk_root/src" "$work_dir/source/sdk/src"
uv build --offline --wheel --out-dir "$work_dir/dist" "$work_dir/source/forge"
uv build --offline --wheel --out-dir "$work_dir/dist" "$work_dir/source/sdk"
forge_wheel=("$work_dir"/dist/forge_puppeteer-*.whl)
sdk_wheel=("$work_dir"/dist/soarm101_motion_sdk-*.whl)
[[ -f "${forge_wheel[0]}" && -f "${sdk_wheel[0]}" ]] || {
  echo "expected Forge Puppeteer and SO-ARM SDK wheels were not built" >&2; exit 2;
}

if [[ -n "$wheelhouse_input" ]]; then
  [[ -d "$wheelhouse_input" ]] || { echo "wheelhouse not found: $wheelhouse_input" >&2; exit 2; }
  compgen -G "$wheelhouse_input/*.whl" >/dev/null || {
    echo "wheelhouse has no wheels: $wheelhouse_input" >&2; exit 2;
  }
  cp "$wheelhouse_input"/*.whl "$work_dir/wheelhouse/"
else
  # This is the only network-capable phase. Installation below always has no index.
  python3.12 -m pip download --dest "$work_dir/wheelhouse" \
    "${forge_wheel[0]}[mcp]" "${sdk_wheel[0]}"
fi
cp "${forge_wheel[0]}" "${sdk_wheel[0]}" "$work_dir/wheelhouse/"

uv venv --offline --python 3.12 "$work_dir/venv"
uv pip install --offline --no-index --find-links "$work_dir/wheelhouse" \
  --python "$work_dir/venv/bin/python" 'forge-puppeteer[mcp]' 'soarm101-motion-sdk'

# -I ignores current directory, PYTHONPATH and user site. Only installed wheels
# are importable by broker, MCP server and the integration harness.
env -u PYTHONPATH PYTHONNOUSERSITE=1 \
  "$work_dir/venv/bin/python" -I "$forge_root/scripts/soarm101-wheel-stdio-smoke.py"
