#!/usr/bin/env bash

PATH="$PATH:/usr/bin:/usr/local/bin:/usr/sbin:/usr/local/sbin:/bin:/sbin"
log="logger -t autoplay.sh -s "

usage()
{
    ${log} "Usage: $0 {add|remove} device_name (e.g. sdb1)"
    exit 1
}

if [[ $# -ne 2 ]]; then
    usage
fi

ACTION=$1
DEVBASE=$2
DEVICE="/dev/${DEVBASE}"
SCREENLY="http://localhost/api/v1.1/assets"

# See if this drive is already mounted, and if so where
MOUNT_POINT=$(mount | grep ${DEVICE} | awk '{ print $3 }')

DEV_LABEL=""

add_assets()
{
    START_DATE=$(date +%Y-%m-%dT%H:%M:%S.000Z --date=-1day)
    END_DATE=$(date +%Y-%m-%dT%H:%M:%S.000Z --date=+1year)

    find $MOUNT_POINT -type f |
    xargs file --mime-type |
    sed 's/: / /' |
    while read FILE MIME; do
        NAME=$(basename "$FILE")
        ID=$RANDOM$RANDOM
        curl -X POST "$SCREENLY" \
            -H "Content-type: application/json" \
            -d "{\
                \"is_enabled\":1,\
                \"mimetype\":\"$MIME\",\
                \"end_date\":\"$END_DATE\",\
                \"is_active\":1,\
                \"duration\":10,\
                \"asset_id\":\"$ID\",\
                \"name\":\"$NAME\",\
                \"uri\":\"$FILE\",\
                \"start_date\":\"$START_DATE\"\
            }"
    done
}

clean_assets()
{
    curl -X GET "$SCREENLY" |
    tr '}' '\n' |
    grep "\"uri\": *\"$MOUNT_POINT" |
    sed -n 's/.*"asset_id": *"\([^"]*\)".*/\1/p' |
    xargs -i curl -X DELETE "$SCREENLY/{}"
}

case "${ACTION}" in
    add)
        add_assets
        ;;
    remove)
        clean_assets
        ;;
    *)
        usage
        ;;
esac
