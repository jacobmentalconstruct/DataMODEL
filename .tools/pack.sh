#!/bin/sh
# Pack this project into <ProjectName>.zip beside the project folder (or: sh .tools/pack.sh /out/name.zip).
# Leaves out this copy's identity, receipts, journal and snapshots; writes unpack launchers beside the zip.
here="$(cd "$(dirname "$0")" && pwd)"
if command -v python3 >/dev/null 2>&1; then exec python3 "$here/bin/helpers.py" pack "$@"; fi
exec python "$here/bin/helpers.py" pack "$@"
