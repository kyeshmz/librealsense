#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$#" -ne 2 ]]; then
    echo "usage: bash scripts/jetson/check-device.sh TARGET BACKEND" >&2
    exit 2
fi

TARGET="$1"
BACKEND="$2"
case "$TARGET" in
    jp4|jp5|jp6|jp7) ;;
    *) echo "unknown Jetson target: $TARGET" >&2; exit 2 ;;
esac
case "$BACKEND" in
    rsusb|native) ;;
    *) echo "unknown backend: $BACKEND" >&2; exit 2 ;;
esac

python3 "$SCRIPT_DIR/jetson_ci.py" device-check --target "$TARGET" --backend "$BACKEND"
