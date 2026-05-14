#!/usr/bin/env bash
# Generate the 8-clip board-enablement test pack used by
# docs/board-enablement.md.
#
# Output: 4 H.264 + 4 HEVC clips at 1080p30/60 + 4K30/60, each ~1
# minute. That's long enough to read a stable Dropped: count and
# capture mpv's hwdec-current banner, short enough that re-encoding
# the whole pack on a Pi 4 doesn't take an afternoon.
#
# Idempotent: clips that already exist (and pass an ffprobe sanity
# check) are skipped. Re-run after a power cycle and the script
# only redoes what's missing.
#
# Usage:
#   bash bin/generate_board_enablement_testbed.sh [DEST_DIR]
#
# DEST_DIR defaults to ~/bbb-testbed. Set EARLY_EXIT=1 to stop on
# the first missing output (useful when iterating on a single clip
# in CI).

set -euo pipefail

DEST="${1:-$HOME/bbb-testbed}"
CUT_SECONDS="${CUT_SECONDS:-60}"
HEVC_CRF="${HEVC_CRF:-23}"
BBB_BASE='https://download.blender.org/demo/movies/BBB'

mkdir -p "$DEST"
cd "$DEST"

log() { printf '[testbed] %s\n' "$*" >&2; }

# Returns 0 if $1 exists and ffprobe can parse it. Used so a
# previous half-written file (power cycle, ctrl-C) doesn't get
# silently kept on a re-run.
file_ok() {
    [[ -s "$1" ]] || return 1
    ffprobe -v error -show_format -of default=nw=1:nk=1 "$1" \
        > /dev/null 2>&1 || return 1
    return 0
}

# Sources: H.264 + AAC, full-length, from blender.org. We trim
# rather than downloading shorter variants because the trims also
# exercise the upload path's handling of files whose container
# trailer isn't strictly mp4-spec-aligned (ffmpeg's mp4 muxer is
# tolerant; the Pi V3D V4L2 M2M decoder less so).
SOURCES=(
    bbb_sunflower_1080p_30fps_normal.mp4
    bbb_sunflower_1080p_60fps_normal.mp4
    bbb_sunflower_2160p_30fps_normal.mp4
    bbb_sunflower_2160p_60fps_normal.mp4
)

log "step 1: download originals (skips existing)"
for f in "${SOURCES[@]}"; do
    if file_ok "$f"; then
        log "  $f: present"
        continue
    fi
    url="$BBB_BASE/$f"
    log "  $f: downloading from $url"
    curl -sSL --fail -o "$f.tmp" "$url"
    mv "$f.tmp" "$f"
done

# Step 2: cut H.264 sources to CUT_SECONDS via -c copy. Avoids a
# re-encode (instant on any host), keeps the bitstream identical
# so the resulting clip exercises the same V3D / Hantro G1 path as
# the full-length original.
declare -A H264_TARGETS=(
    [bbb_1080p_30fps.mp4]=bbb_sunflower_1080p_30fps_normal.mp4
    [bbb_1080p_60fps.mp4]=bbb_sunflower_1080p_60fps_normal.mp4
    [bbb_4k_30fps.mp4]=bbb_sunflower_2160p_30fps_normal.mp4
    [bbb_4k_60fps.mp4]=bbb_sunflower_2160p_60fps_normal.mp4
)

log "step 2: trim H.264 sources to ${CUT_SECONDS}s (-c copy)"
for out in "${!H264_TARGETS[@]}"; do
    src="${H264_TARGETS[$out]}"
    if file_ok "$out"; then
        log "  $out: present"
        continue
    fi
    log "  $out: trim from $src"
    ffmpeg -hide_banner -loglevel error -y \
        -ss 0 -t "$CUT_SECONDS" -i "$src" \
        -c copy -avoid_negative_ts make_zero "$out.tmp.mp4"
    mv "$out.tmp.mp4" "$out"
done

# Step 3: HEVC re-encode each H.264 cut. ``-tag:v hvc1`` writes
# the iOS-friendly codec tag (matches what the asset processor
# emits at upload time -- keeps the cross-board fleet sha256 test
# meaningful). CRF defaults to 23 to roughly match the source's
# perceived quality so a passthrough vs re-encode A/B comparison
# isn't muddied by a visible quality delta.
declare -A HEVC_TARGETS=(
    [bbb_1080p_30fps_hevc.mp4]=bbb_1080p_30fps.mp4
    [bbb_1080p_60fps_hevc.mp4]=bbb_1080p_60fps.mp4
    [bbb_4k_30fps_hevc.mp4]=bbb_4k_30fps.mp4
    [bbb_4k_60fps_hevc.mp4]=bbb_4k_60fps.mp4
)

log "step 3: HEVC re-encode (libx265 preset=medium crf=$HEVC_CRF)"
for out in "${!HEVC_TARGETS[@]}"; do
    src="${HEVC_TARGETS[$out]}"
    if file_ok "$out"; then
        log "  $out: present"
        continue
    fi
    log "  $out: encode from $src (this can take a minute or two)"
    ffmpeg -hide_banner -loglevel error -y \
        -i "$src" \
        -c:v libx265 -preset medium -crf "$HEVC_CRF" -tag:v hvc1 \
        -c:a copy "$out.tmp.mp4"
    mv "$out.tmp.mp4" "$out"
done

# Step 4: report.
log "step 4: pack summary"
printf '\n%-32s %-10s %-12s %-10s %-8s\n' \
    file codec resolution fps duration_s
printf '%-32s %-10s %-12s %-10s %-8s\n' \
    -------- ----- ---------- --- ---
for f in bbb_1080p_30fps.mp4 bbb_1080p_60fps.mp4 \
         bbb_4k_30fps.mp4 bbb_4k_60fps.mp4 \
         bbb_1080p_30fps_hevc.mp4 bbb_1080p_60fps_hevc.mp4 \
         bbb_4k_30fps_hevc.mp4 bbb_4k_60fps_hevc.mp4; do
    [[ -f "$f" ]] || continue
    codec=$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=codec_name -of csv=p=0 "$f")
    width=$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=width -of csv=p=0 "$f")
    height=$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=height -of csv=p=0 "$f")
    fps_rat=$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=r_frame_rate -of csv=p=0 "$f")
    fps=$(echo "$fps_rat" | awk -F/ '{ printf "%.0f", $1/$2 }')
    dur=$(ffprobe -v error \
        -show_entries format=duration -of csv=p=0 "$f")
    printf '%-32s %-10s %-12s %-10s %-8s\n' \
        "$f" "$codec" "${width}x${height}" "$fps" \
        "$(printf '%.1f' "$dur")"
done

log "done. pack lives at $DEST"
