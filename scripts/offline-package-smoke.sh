#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sdk_root="${STACKCHAN_SDK_ROOT:-/home/larry/code/stackchan-control-sdk}"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

wheelhouse="${1:-}"
if [[ $# -gt 1 ]]; then
  echo "usage: $0 [wheelhouse-directory]" >&2
  exit 2
fi

command -v uv >/dev/null || { echo "uv is required" >&2; exit 2; }
[[ -f "$sdk_root/pyproject.toml" ]] || { echo "StackChan SDK source not found: $sdk_root" >&2; exit 2; }
mkdir -p "$work_dir/source" "$work_dir/wheelhouse"
cp "$repo_root/pyproject.toml" "$repo_root/README.md" "$work_dir/source/"
cp -a "$repo_root/src" "$work_dir/source/src"
uv build --offline --wheel --out-dir "$work_dir/dist" "$work_dir/source"
uv build --offline --wheel --out-dir "$work_dir/dist" "$sdk_root"
forge_wheel=("$work_dir"/dist/forge_puppeteer-*.whl)
stackchan_wheel=("$work_dir"/dist/stackchan_control-*.whl)
# Stage the two freshly built wheels and runtime dependencies before the
# install step. Supplying a wheelhouse makes staging itself index-free too.
if [[ -n "$wheelhouse" ]]; then
  [[ -d "$wheelhouse" ]] || { echo "wheelhouse not found: $wheelhouse" >&2; exit 2; }
  compgen -G "$wheelhouse/*.whl" >/dev/null || { echo "wheelhouse has no wheels: $wheelhouse" >&2; exit 2; }
  cp "$wheelhouse"/*.whl "$work_dir/wheelhouse/"
else
  python3.12 -m pip download --dest "$work_dir/wheelhouse" \
    "${forge_wheel[0]}[mcp]" "${stackchan_wheel[0]}[serial]"
fi
uv venv --offline --python 3.12 "$work_dir/venv"
cp "${forge_wheel[0]}" "${stackchan_wheel[0]}" "$work_dir/wheelhouse/"
uv pip install --offline --no-index --find-links "$work_dir/wheelhouse" \
  --python "$work_dir/venv/bin/python" \
  'forge-puppeteer[mcp]' 'stackchan-control[serial]'

export PYTHONNOUSERSITE=1
export PATH="$work_dir/venv/bin:$PATH"
"$work_dir/venv/bin/forge-puppeteer" validate \
  --request "$repo_root/examples/robots/package-smoke-request.json"
"$work_dir/venv/bin/forge-puppeteer" execute \
  --request "$repo_root/examples/robots/package-smoke-request.json" \
  --stage mock --out "$work_dir/take.json" --output-dir "$work_dir/output"
test -s "$work_dir/take.json"
"$work_dir/venv/bin/python" "$repo_root/scripts/stdio-package-smoke.py"
"$work_dir/venv/bin/python" "$repo_root/scripts/downstream-integration-smoke.py"
"$work_dir/venv/bin/python" "$repo_root/scripts/stackchan-pty-stdio-smoke.py"
"$work_dir/venv/bin/python" "$repo_root/scripts/reachy-sdk-wheel-stdio-smoke.py"
echo "offline wheel install, mock CLI, MCP stdio profiles, Studios/Reachy downstream handoff, Reachy fake-SDK wheel lifecycle, SO-ARM fake-broker delegation, and StackChan PTY SDK launcher smoke passed"
