FROM balenalib/rpi-raspbian:buster

# This list can most likely be slimmed down *a lot* but that's for another day.
RUN apt-get update && \
    apt-get -y install --no-install-recommends \
        bison \
        build-essential \
        cowsay \
        flex \
        freetds-dev \
        g++ \
        git \
        gperf \
        gyp \
        libasound2-dev \
        libavcodec-dev \
        libavformat-dev \
        libavutil-dev \
        libbz2-dev \
        libcap-dev \
        libcups2-dev \
        libdbus-1-dev \
        libdbus-glib-1-dev \
        libdrm-dev \
        libegl1-mesa-dev \
        libevent-dev \
        libfontconfig1-dev \
        libgbm-dev \
        libgcrypt20-dev \
        libgles2-mesa-dev \
        libinput-dev \
        libjsoncpp-dev \
        libminizip-dev \
        libnss3-dev \
        libopus-dev \
        libpci-dev \
        libpulse-dev \
        libqt5webchannel5-dev/stable \
        libraspberrypi-dev \
        libraspberrypi0 \
        libsnappy-dev \
        libsrtp0-dev \
        libsrtp2-dev \
        libssl-dev \
        libts-dev \
        libudev-dev \
        libvpx-dev \
        libwebp-dev \
        libxcb-xinerama0 \
        libxcb-xinerama0-dev \
        libxcomposite-dev \
        libxcursor-dev \
        libxdamage-dev \
        libxrandr-dev \
        libxss-dev \
        libxtst-dev \
        lsb-release \
        ninja-build \
        nodejs \
        python \
        qt5-default \
        qtbase5-private-dev/stable \
        qtcreator \
        qtdeclarative5-private-dev/stable  \
        ruby \
        wget \
        make && \
    apt-get clean

WORKDIR /build

COPY build_qtbase.sh /usr/local/bin/
CMD /usr/local/bin/build_qtbase.sh
