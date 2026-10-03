#!/usr/bin/env bash
# Renders the standard visual-QA screenshots into docs/img/ (or $SHOTS_OUT) with tools/screenshot.gd under xvfb.
#
#   godot/tools/shots.sh            the standard set (about 30 images, a few minutes)
#   godot/tools/shots.sh --quick    only the minimal overview (CI smoke)
#   godot/tools/shots.sh --only=<substring>   only images whose file name contains the substring
#
# Environment: GODOT = path to the Godot 4.6 binary (default: `godot` on PATH), SHOTS_OUT = output directory.
# A PNG above 400 KB is reduced to a 256-colour palette (ImageMagick `convert`), and rendered again at half size if
# it is still above the limit.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "$HERE/.." && pwd)"
OUT="${SHOTS_OUT:-$PROJECT/../docs/img}"
GODOT="${GODOT:-$(command -v godot || true)}"
if [ -z "$GODOT" ] || [ ! -x "$GODOT" ]; then
    echo "shots.sh: set GODOT to the Godot 4.6 binary" >&2
    exit 2
fi
MAX_BYTES=400000
QUICK=0
ONLY=""
for a in "$@"; do
    case "$a" in
        --quick) QUICK=1 ;;
        --only=*) ONLY="${a#--only=}" ;;
        *) echo "shots.sh: unknown argument $a" >&2; exit 2 ;;
    esac
done
mkdir -p "$OUT"
FAILED=0
COUNT=0

# shot <file name without .png> <size WxH> <screenshot.gd args...>
shot() {
    local name="$1" size="$2"; shift 2
    if [ -n "$ONLY" ] && [[ "$name" != *"$ONLY"* ]]; then return; fi
    local file="$OUT/$name.png"
    local extra=("$@")
    local log
    if ! log=$(xvfb-run -a -s "-screen 0 ${size}x24" "$GODOT" --path "$PROJECT" --rendering-driver opengl3 \
            --script res://tools/screenshot.gd -- --size="$size" --out="$file" "${extra[@]}" 2>&1); then
        echo "FAILED $name"; echo "$log" | grep -E "screenshot:|SCRIPT ERROR|Parse Error" | head -5; FAILED=$((FAILED + 1)); return
    fi
    local bytes; bytes=$(stat -c %s "$file" 2>/dev/null || echo 0)
    if [ "$bytes" -gt "$MAX_BYTES" ] && command -v convert >/dev/null 2>&1; then
        # 256-colour palette: visually the same for these flat-shaded frames, about 60 % smaller
        convert "$file" -colors 256 "PNG8:$file.tmp" 2>/dev/null && mv "$file.tmp" "$file"
        bytes=$(stat -c %s "$file" 2>/dev/null || echo 0)
    fi
    if [ "$bytes" -gt "$MAX_BYTES" ]; then
        xvfb-run -a -s "-screen 0 ${size}x24" "$GODOT" --path "$PROJECT" --rendering-driver opengl3 \
            --script res://tools/screenshot.gd -- --size="$size" --out="$file" --scale=0.5 "${extra[@]}" >/dev/null 2>&1
        bytes=$(stat -c %s "$file" 2>/dev/null || echo 0)
    fi
    COUNT=$((COUNT + 1))
    echo "ok $name ($((bytes / 1024)) KB)"
}

if [ "$QUICK" = 1 ]; then
    shot minimal_overview_w0 1280x720 --scenario=minimal --weeks=0 --view=overview
    [ "$FAILED" = 0 ] || exit 1
    exit 0
fi

for sc in minimal healthcare_standard industrial_standard civil_standard healthcare_manual_demo; do
    for w in 0 15 30; do
        shot "${sc}_overview_w${w}" 1280x720 --scenario=$sc --weeks=$w --view=overview --hide=gantt
    done
done

HC="--scenario=healthcare_standard --weeks=15"
shot healthcare_standard_overview_w15_1920 1920x1080 $HC --view=overview
shot healthcare_standard_overview_w15_gantt 1280x720 $HC --view=overview --panels=gantt
shot healthcare_standard_overview_w15_editor 1280x720 $HC --view=overview --panels=editor --zone=L00-Z3
shot healthcare_standard_overview_w15_whats_needed 1280x720 $HC --view=overview --panels=whats_needed --zone=L00-Z3
shot healthcare_standard_overview_w15_procurement_crews 1280x720 $HC --view=overview --panels=procurement,crews --hide=inspector,gantt
shot healthcare_standard_overview_w15_report 1280x720 $HC --view=overview --panels=report --hide=gantt
shot healthcare_standard_overview_w15_final 1280x720 $HC --view=overview --panels=final --hide=gantt
shot healthcare_standard_overview_w15_toast 1280x720 $HC --view=overview --panels=toast --hide=gantt
shot healthcare_standard_zone-L00-Z3_w15 1280x720 $HC --view=zone:L00-Z3 --hide=gantt
shot healthcare_standard_storey-1_w15 1280x720 $HC --view=storey:1 --hide=gantt
shot healthcare_standard_overview_w15_gantt_1920 1920x1080 $HC --view=overview --panels=gantt
shot healthcare_manual_demo_overview_w15_editor_legend 1280x720 --scenario=healthcare_manual_demo --weeks=15 --view=overview --panels=editor,legend --zone=L00-Z3
shot industrial_standard_installation-24_w20 1280x720 --scenario=industrial_standard --weeks=20 --view=installation:24 --pad=2 --panels=installations --hide=gantt,crews,charts,inspector,procurement
shot industrial_standard_installation-31_w20 1280x720 --scenario=industrial_standard --weeks=20 --view=installation:31 --pad=1 --panels=installations --hide=gantt,crews,charts,inspector,procurement
shot industrial_installations 1280x720 --scenario=industrial_standard --weeks=40 --view=installation:25 --pad=4 --panels=installations --hide=gantt,crews,charts,inspector,procurement
shot industrial_standard_overview_w20_heat 1280x720 --scenario=industrial_standard --weeks=20 --view=overview --panels=heat --hide=gantt

echo "shots.sh: $COUNT images written to $OUT, $FAILED failed"
[ "$FAILED" = 0 ]
