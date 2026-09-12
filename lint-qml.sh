#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
shell_dir="${OMARCHY_PATH:-/usr/share/omarchy}/shell"
import_dir="$(mktemp -d)"
trap 'rm -rf -- "$import_dir"' EXIT
ln -s -- "$shell_dir" "$import_dir/qs"
/usr/lib/qt6/bin/qmllint -I "$import_dir" Panel.qml
