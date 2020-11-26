FROM balenalib/rpi-raspbian:buster

RUN echo "deb-src http://archive.raspberrypi.org/debian stretch main ui" >> /etc/apt/sources.list.d/raspi.list
RUN echo "deb-src http://archive.raspbian.org/raspbian stretch main contrib non-free rpi firmware" >> /etc/apt/sources.list

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
        libbz2-dev \
        libcap-dev \
        libdrm-dev \
        libegl1-mesa-dev \
        libgbm-dev \
        libgles2-mesa-dev \
        libinput-dev \
        libnss3-dev \
        libpci-dev \
        libpulse-dev \
        libraspberrypi-dev \
        libraspberrypi0 \
        libsnappy-dev \
        libsrtp0-dev \
        libts-dev \
        libudev-dev \
        libvpx-dev \
        libwebp-dev \
        libxcb-xinerama0 \
        libxcb-xinerama0-dev \
        libxtst-dev \
        lsb-release \
        ninja-build \
        nodejs \
        python \
        qtdeclarative5-private-dev \
        ruby \
        wget \
        make && \
    apt-get build-dep libqt5gui5 && \
    apt-get clean

WORKDIR /build

COPY build_qtbase.sh /usr/local/bin/
CMD /usr/local/bin/build_qtbase.sh
