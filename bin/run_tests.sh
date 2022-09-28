#!/bin/bash

prepare () {
    mkdir -p ~/.screenly ~/.config/uzbl/ ~/screenly_assets /tmp/USB/cleanup_folder
    # cp ansible/roles/screenly/files/uzbl-config ~/.config/uzbl/config-screenly
    cp ansible/roles/screenly/files/screenly.conf ~/.screenly/
    cp ansible/roles/screenly/files/screenly.db ~/.screenly/
    cp bin/install.sh /usr/local/sbin/upgrade_screenly.sh
    chmod 0700 /usr/local/sbin/upgrade_screenly.sh
    echo -e "[local]\nlocalhost ansible_connection=local" > ansible/localhost
    curl https://www.screenly.io/upload/ose-logo.png > /tmp/image.png
    cp /tmp/image.png /tmp/USB/image.png
    cp /tmp/image.png /tmp/USB/cleanup_folder/image.png
    curl https://www.screenly.io/upload/ose-logo.png > ~/screenly_assets/image.tmp
    curl https://www.screenly.io/upload/big_buck_bunny_720p_10mb.flv > /tmp/video.flv
    cp tests/assets/asset.mov /tmp/asset.mov
    export DISPLAY=:99.0
    # /sbin/start-stop-daemon --start --quiet \
    #     --pidfile /tmp/custom_xvfb_99.pid --make-pidfile \
    #     --background --exec /usr/bin/Xvfb -- :99 -ac -screen 0 1280x1024x16
    cp tests/config/ffserver.conf /etc/ffserver.conf
    # /usr/bin/ffserver -f /etc/ffserver.conf &
    # sleep 3
    python server.py &
    sleep 3 # give xvfb some time to start
}

execute_tests () {
    find . ! -path "*/rtmplite/*" -name \*.py -exec pep8 --ignore=E402,E501,E731 {} +
    nosetests -v -a '!fixme'
    # ansible-playbook --syntax-check -i ansible/localhost ansible/site.yml
    # python -m SimpleHTTPServer 8081 &
    # sleep 3
    # bash static/spec/runner.sh
}

prepare
execute_tests
