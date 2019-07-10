#!/bin/bash
docker build . -t screenly-ose-dev

docker run --rm -it screenly-ose-dev bash -c '
  mkdir -p ~/.screenly ~/.config/uzbl/ ~/screenly_assets
  cp ansible/roles/screenly/files/uzbl-config ~/.config/uzbl/config-screenly
  cp ansible/roles/screenly/files/screenly.conf ~/.screenly/
  cp ansible/roles/screenly/files/screenly.db ~/.screenly/
  cp ansible/roles/screenly/files/screenly_utils.sh /tmp/screenly_utils.sh
  echo -e "[local]\nlocalhost ansible_connection=local" > ansible/localhost
  curl https://www.screenly.io/upload/ose-logo.png > /tmp/image.png
  curl https://www.screenly.io/upload/ose-logo.png > ~/screenly_assets/image.tmp
  curl https://www.screenly.io/upload/big_buck_bunny_720p_10mb.flv > /tmp/video.flv
  export DISPLAY=:99.0
  /sbin/start-stop-daemon --start --quiet --pidfile /tmp/custom_xvfb_99.pid --make-pidfile --background --exec /usr/bin/Xvfb -- :99 -ac -screen 0 1280x1024x16
  python tests/rtmplite/rtmp.py -r /tmp/ &
  sleep 3
  python server.py &
  sleep 3 # give xvfb some time to start
  nosetests -v -a "!fixme" '
