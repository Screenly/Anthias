#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

bash ./bin/prepare_test_environment.sh
python server.py &
server_pid=$!
sleep 3

nosetests -v -a '!fixme'

if [ -n "$server_pid" ]; then
    kill $server_pid
fi
