#!/bin/bash
#
# Update PHaRLAP / IRI-2020 solar & geomagnetic index files.
#
# IRI-2020 reads two files at runtime to look up F10.7, Ap, IG12, and Rz12
# for the requested date. PHaRLAP ships them as a snapshot, which goes stale
# fast — when the requested date falls past the last entry, IRI prints
#   APF_ONLY: Date is outside range of F10.7D indices file ...
# and silently falls back to climatology, hurting ray-trace accuracy.
#
# Run via systemd timer (hamsci-physics-iri-update.timer), daily. Output goes
# to the systemd journal.
#
# Files refreshed, in EVERY IRI data directory present:
#   apf107.dat   (daily F10.7 + Ap)
#   ig_rz.dat    (monthly IG12 + Rz12)
# in PHaRLAP's /opt/pharlap_4.7.4/dat/iri2020 where PHaRLAP is installed, and
# in each sigmond venv's iri2020 package (site-packages/iri2020/data), which
# is the copy hf-timestd's IRI actually reads on most stations.  Until
# 2026-11-13 only PHaRLAP's copy was refreshed, the timer was never enabled,
# and B4's package copy ended 2023-06-29: IRI ran on climatology fleet-wide.
#
# IRI_DAT_DIRS (space-separated) and IRI_SOURCES override discovery and the
# download sources, for tests.
#
# Sources (in priority order):
#   1. chain-new.chain-project.net (eCHAIM mirror — updated daily)
#   2. irimodel.org (IRI project — slower update cadence)

set -euo pipefail

UA="hamsci-physics/update-iri-indices (https://github.com/HamSCI/hamsci-physics)"

PRIMARY="https://chain-new.chain-project.net/echaim_downloads"
FALLBACK="https://irimodel.org/indices"
SOURCES=${IRI_SOURCES:-"$PRIMARY $FALLBACK"}

if [ -n "${IRI_DAT_DIRS:-}" ]; then
    read -r -a DAT_DIRS <<<"$IRI_DAT_DIRS"
else
    DAT_DIRS=()
    [ -d /opt/pharlap_4.7.4/dat/iri2020 ] && DAT_DIRS+=(/opt/pharlap_4.7.4/dat/iri2020)
    for d in /opt/git/sigmond/*/venv/lib/python3*/site-packages/iri2020/data; do
        [ -f "$d/apf107.dat" ] && DAT_DIRS+=("$d")
    done
fi
if [ ${#DAT_DIRS[@]} -eq 0 ]; then
    echo "No IRI data directory found (no PHaRLAP, no iri2020 package); nothing to update."
    exit 0
fi
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

fetch_file() {
    local fname="$1"
    local tmp="$WORK/$fname"

    for base in $SOURCES; do
        local url="$base/$fname"
        echo "Fetching $url"
        if curl -sSf --max-time 60 -A "$UA" -o "$tmp" "$url"; then
            local size
            size=$(stat -c %s "$tmp")
            if [ "$size" -lt 1024 ]; then
                echo "  rejected: $url returned only $size bytes"
                continue
            fi
            # Sanity: last non-empty line should be ASCII (basic format check)
            if ! tail -1 "$tmp" | grep -q '[0-9]'; then
                echo "  rejected: $url last line has no digits"
                continue
            fi
            local last
            last=$(tail -1 "$tmp" | tr -s ' ' | head -c 60)
            echo "  ok: $size bytes, last line: $last"
            # Install into every directory, each by a rename beside the target, so
            # a reader never sees half a file; mode 0644 so every user reads it.
            local d
            for d in "${DAT_DIRS[@]}"; do
                local next="$d/.$fname.new"
                cp "$tmp" "$next" && chmod 0644 "$next" && mv "$next" "$d/$fname" \
                    && echo "  installed $d/$fname" || echo "  FAILED to install $d/$fname"
            done
            return 0
        fi
        echo "  curl failed for $url"
    done

    echo "ERROR: all sources failed for $fname"
    return 1
}

echo "Updating IRI-2020 indices in: ${DAT_DIRS[*]}"
fetch_file apf107.dat
fetch_file ig_rz.dat
echo "Done."
