#!/usr/bin/env sh
set -eu
APP=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(dirname -- "$APP")
WHEEL=
CYUI=
if [ "$#" -ge 1 ]; then WHEEL=$1; fi
if [ "$#" -ge 2 ]; then CYUI=$2; fi
if [ -z "$WHEEL" ]; then
    for candidate in "$ROOT"/runtime/packages/cytools_core-0.12.0-*.whl; do
        [ -f "$candidate" ] && WHEEL=$candidate && break
    done
fi
if [ -z "$WHEEL" ]; then
    echo 'Pass the CyToolsCore 0.12.0 Linux wheel as the first argument.' >&2
    exit 1
fi
if [ -z "$CYUI" ]; then
    for candidate in "$ROOT"/runtime/packages/cyui-1.2.0-*.whl; do
        [ -f "$candidate" ] && CYUI=$candidate && break
    done
fi
if [ -z "$CYUI" ]; then
    CYUI='cyui @ git+https://github.com/MrMybal/CyUI.git@6ef01cfcdb197f3a89d64618ce5ef8818c704728'
fi
python3 -m venv "$ROOT/runtime/python"
"$ROOT/runtime/python/bin/python" -m pip install "$WHEEL" "$CYUI" -r "$APP/requirements.txt"
