#!/bin/sh
set -eu
umask 077
dts_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$dts_dir/source/entry.py" "$@"
