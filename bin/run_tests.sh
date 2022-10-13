#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

python server.py &
server_pid=$!
sleep 3

# The command below will allow you to run all tests that don't have a `fixme` attribute.
nosetests -v -a '!fixme'

if [ -n "$server_pid" ]; then
    kill $server_pid
fi
