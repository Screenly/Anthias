#!/bin/sh
# Repair a damaged append-only file before Redis reads it.
#
# A power cut or a failing card while Redis appends to the incremental
# AOF can leave a bad record in the middle of the file. Redis treats
# that as fatal ("Bad file format reading the append only file") and
# exits. `restart: always` then puts it straight back into the same
# failure, and nothing ever repairs the file, so the player stays down
# for good: celery cannot reach its broker, the splash page returns 500
# and the viewer stops following the playlist.
#
# Every start is checked, which costs milliseconds on an AOF this
# small. A damaged AOF is handled in two steps:
#
#   1. `redis-check-aof --fix` truncates the file at the first bad
#      record. Everything written before it survives, including the
#      telemetry device_id this image persists the AOF for.
#   2. If that cannot produce a valid AOF (the base file itself is
#      damaged), the AOF directory is moved aside and a new one is
#      seeded from the last RDB snapshot, if that is intact. Redis 7+
#      with appendonly enabled ignores dump.rdb when the AOF directory
#      is missing, so without the seed it would start empty.
#
# Either way Redis starts. Everything a player keeps in Redis can be
# rebuilt; a Redis that never starts takes the whole player down.
set -u

data_dir=/var/lib/redis
previous=
for argument in "$@"; do
    if [ "$previous" = '--dir' ]; then
        data_dir=$argument
    fi
    previous=$argument
done

aof_dir="$data_dir/appendonlydir"
manifest="$aof_dir/appendonly.aof.manifest"

log() {
    echo "redis-entrypoint: $*"
}

aof_is_valid() {
    redis-check-aof "$manifest" >/dev/null 2>&1
}

seed_aof() {
    snapshot="$data_dir/dump.rdb"
    if [ -f "$snapshot" ] && redis-check-rdb "$snapshot" >/dev/null 2>&1; then
        # Publish the manifest only after the base is complete. Until then,
        # the quarantined directory lets the next boot resume this recovery.
        if ! mkdir -p "$aof_dir" ||
           ! cp "$snapshot" "$aof_dir/appendonly.aof.1.base.rdb" ||
           ! printf 'file appendonly.aof.1.base.rdb seq 1 type b\n' > "$manifest.recovery" ||
           ! mv "$manifest.recovery" "$manifest"; then
            log 'could not rebuild the append-only file from the snapshot'
            return 1
        fi
        log 'rebuilt the append-only file from the last RDB snapshot'
    else
        log 'no usable RDB snapshot; starting with an empty dataset'
    fi
}

recover_aof() {
    log 'the append-only file is damaged; truncating it at the first bad record'
    echo y | redis-check-aof --fix "$manifest" >/dev/null 2>&1
    if aof_is_valid; then
        log 'repaired the append-only file'
        return
    fi

    log 'could not repair the append-only file; moving it aside'
    if ! rm -rf "$aof_dir.corrupt" || ! mv "$aof_dir" "$aof_dir.corrupt"; then
        log 'could not move the damaged append-only file aside'
        return 1
    fi
    seed_aof
}

if [ -f "$manifest" ] && ! aof_is_valid; then
    recover_aof || exit 1
elif [ ! -f "$manifest" ] && [ -d "$aof_dir.corrupt" ]; then
    log 'resuming an interrupted append-only file recovery'
    seed_aof || exit 1
fi

exec "$@"
