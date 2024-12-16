#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

START_SERVER=false

print_usage () {
    echo "Usage: $0 [-s]"
    echo "Example: $0 -s"
    echo
    echo "Options:"
    echo "  -s    run the web server (server.py)"
}

while getopts "sh" arg; do
    case "${arg}" in
        h)
            print_usage
            exit 0
            ;;
        s)
            START_SERVER=true
            ;;
        *)
            print_usage
            exit 0
            ;;
    esac
done

cp tests/assets/asset.mov /tmp/asset.mov
cp static/img/standby.png /tmp/image.png
cp tests/config/ffserver.conf /etc/ffserver.conf

cat << 'EOF' > $HOME/.bashrc
#!/bin/bash

export PATH=$PATH:/opt/chrome-linux64:/opt/chromedriver-linux64
EOF

# @TODO: Uncomment the lines below when test_add_asset_streaming is fixed.
# nohup /opt/ffmpeg/ffserver -f /etc/ffserver.conf > /dev/null 2>&1 &
# sleep 3

if [ "$START_SERVER" = true ]; then
    cd /usr/src/app

    npm install && npm run build

    ./manage.py makemigrations
    ./manage.py migrate --fake-initial
    ./manage.py runserver 127.0.0.1:8080 &

    sleep 3
fi
