FROM --platform=linux/arm/v7 balenalib/raspberrypi3:buster as builder

# There are likely a large number of dependencies that can be stripped out here
# depending on your needs (and probably in general). My primary objective was just
# to make things work.
RUN apt-get update && \
    apt-get install -y \
        apt-utils \
        firebird-dev \
        freetds-dev \
        gstreamer-tools \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-ugly \
        gstreamer1.0-x \
        libasound2-dev \
        libavcodec-dev \
        libavformat-dev \
        libavutil-dev \
        libbz2-dev \
        libcap-dev \
        libdbus-1-dev \
        libdbus-glib-1-dev \
        libdrm-dev \
        libegl1-mesa-dev \
        libevent-dev \
        libfontconfig1-dev \
        libfreetype6-dev \
        libgbm-dev \
        libgcrypt20-dev \
        libgles2-mesa-dev \
        libglib2.0-dev \
        libgst-dev \
        libgstreamer-plugins-base1.0-dev \
        libgstreamer1.0-dev \
        libicu-dev \
        libinput-dev \
        libiodbc2-dev \
        libjpeg62-turbo-dev \
        libjsoncpp-dev \
        libminizip-dev \
        libnss3-dev \
        libopus-dev \
        libpci-dev \
        libpng-dev \
        libpng16-16 \
        libpq-dev \
        libpulse-dev \
        libraspberrypi-bin \
        libraspberrypi0 \
        librsvg2-common \
        libsnappy-dev \
        libsqlite0-dev \
        libsqlite3-dev \
        libsrtp0-dev \
        libsrtp2-dev \
        libssl-dev \
        libssl1.1 \
        libswscale-dev \
        libsystemd-dev \
        libts-dev \
        libudev-dev \
        libvpx-dev \
        libwayland-dev \
        libwebp-dev \
        libx11-dev \
        libx11-xcb-dev \
        libx11-xcb1 \
        libxcb-glx0-dev \
        libxcb-icccm4 \
        libxcb-icccm4-dev \
        libxcb-image0 \
        libxcb-image0-dev \
        libxcb-keysyms1 \
        libxcb-keysyms1-dev \
        libxcb-randr0-dev \
        libxcb-render-util0 \
        libxcb-render-util0-dev \
        libxcb-shape0-dev \
        libxcb-shm0 \
        libxcb-shm0-dev \
        libxcb-sync-dev \
        libxcb-sync1 \
        libxcb-xfixes0-dev \
        libxcb-xinerama0 \
        libxcb-xinerama0-dev \
        libxcb1 \
        libxcb1-dev \
        libxext-dev \
        libxi-dev \
        libxkbcommon-dev \
        libxrender-dev \
        libxslt1-dev \
        libxss-dev \
        libxtst-dev \
        nodejs \
        ruby \
        va-driver-all \
        wget

# Really make sure we don't have this package installed
# as it will break the build of QTWebEngine
# https://www.enricozini.org/blog/2020/qt5/build-qt5-cross-builder-with-raspbian-sysroot-compiling-with-the-sysroot-continued/
RUN dpkg --purge libraspberrypi-dev

FROM debian:buster

# This list can most likely be slimmed down *a lot* but that's for another day.
RUN apt-get update && \
    apt-get -y install \
        bison \
        build-essential \
        ccache \
        cowsay \
        flex \
        freetds-dev \
        g++ \
        g++-multilib \
        gcc-multilib \
        git \
        gperf \
        gyp \
        lib32z1-dev \
        libasound2 \
        libasound2-dev \
        libavcodec-dev \
        libavformat-dev \
        libavutil-dev \
        libbz2-dev \
        libcap-dev \
        libdbus-1-dev \
        libdbus-glib-1-dev \
        libdrm-dev \
        libegl1-mesa-dev \
        libevent-dev \
        libfontconfig1 \
        libfontconfig1-dev \
        libfreetype6 \
        libgbm-dev \
        libgcrypt20-dev \
        libgles2-mesa-dev \
        libinput-dev \
        libjpeg62-turbo-dev \
        libjsoncpp-dev \
        libminizip-dev \
        libnss3 \
        libnss3-dev \
        libopus-dev \
        libpci-dev \
        libpng16-16 \
        libpulse-dev \
        libsecret-1-0 \
        libsnappy-dev \
        libsrtp2-dev \
        libssl-dev \
        libssl1.1 \
        libtiff5 \
        libts-dev \
        libudev-dev \
        libvpx-dev \
        libwebp-dev \
        libxss-dev \
        libxss1 \
        libxtst-dev \
        lsb-release \
        ninja-build \
        nodejs \
        python \
        rsync \
        ruby \
        subversion \
        wget \
        make && \
    apt-get clean

WORKDIR /build

RUN wget -q https://raw.githubusercontent.com/riscv/riscv-poky/master/scripts/sysroot-relativelinks.py \
        -O /usr/local/bin/sysroot-relativelinks.py && \
    chmod +x /usr/local/bin/sysroot-relativelinks.py

RUN mkdir -p /sysroot/usr /sysroot/opt /sysroot/lib
COPY --from=builder /lib/ /sysroot/lib/
COPY --from=builder /usr/include/ /sysroot/usr/include/
COPY --from=builder /usr/lib/ /sysroot/usr/lib/
COPY --from=builder /opt/vc/ /sysroot/opt/vc/

ENV BUILD_WEBVIEW 1
ENV CCACHE_MAXSIZE 10G
ENV CCACHE_DIR /src/ccache
ARG GIT_HASH=0
ENV GIT_HASH=$GIT_HASH

COPY build_qt5.sh /usr/local/bin/
CMD /usr/local/bin/build_qt5.sh
