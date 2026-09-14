#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_VERSION="1.0.2"
SDK_ROOT="${OBSBOT_SDK_ROOT:-$HOME/.local/share/forge-puppeteer/obsbot-sdk/v${SDK_VERSION}}"
BIN_DIR="${HOME}/.local/bin"
HELPER="${BIN_DIR}/forge-puppeteer-obsbot-helper"

# The OBSBOT Linux SDK used by the proven Tiny 2/Tiny 2 Lite control path is
# distributed in the public obsbot-camera-control repository. Forge Puppeteer
# downloads only the SDK headers/library needed by the native helper.
SDK_BASE="https://raw.githubusercontent.com/aaronsb/obsbot-camera-control/main/sdk/v${SDK_VERSION}"

need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 2
  fi
}

need_command g++
need_command curl

mkdir -p \
  "$SDK_ROOT/include/dev" \
  "$SDK_ROOT/include/util" \
  "$SDK_ROOT/lib" \
  "$BIN_DIR"

download_if_missing() {
  local url="$1"
  local dest="$2"
  if [[ ! -s "$dest" ]]; then
    echo "Downloading $(basename "$dest")..."
    curl --fail --location --silent --show-error "$url" -o "$dest.tmp"
    mv "$dest.tmp" "$dest"
  fi
}

download_if_missing "$SDK_BASE/include/dev/dev.hpp" "$SDK_ROOT/include/dev/dev.hpp"
download_if_missing "$SDK_BASE/include/dev/devs.hpp" "$SDK_ROOT/include/dev/devs.hpp"
download_if_missing "$SDK_BASE/include/util/comm.hpp" "$SDK_ROOT/include/util/comm.hpp"
download_if_missing "$SDK_BASE/lib/libdev.so.1.0.2" "$SDK_ROOT/lib/libdev.so.1.0.2"

ln -sfn libdev.so.1.0.2 "$SDK_ROOT/lib/libdev.so.1"
ln -sfn libdev.so.1 "$SDK_ROOT/lib/libdev.so"

g++ -std=c++17 -O2 \
  -I"$SDK_ROOT/include" \
  "$ROOT/native/obsbot_sdk_helper.cpp" \
  -L"$SDK_ROOT/lib" -Wl,-rpath,"$SDK_ROOT/lib" \
  -ldev -pthread \
  -o "$HELPER"

echo "Installed OBSBOT SDK runtime: $SDK_ROOT"
echo "Installed Forge Puppeteer helper: $HELPER"
echo "No separate obsbot-camera-control application is required at runtime."
