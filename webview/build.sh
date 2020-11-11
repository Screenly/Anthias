#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-
set -euo pipefail

cd /src
/usr/lib/arm-linux-gnueabihf/qt5/bin/qmake
make -j "$(nproc --all)"
make install
