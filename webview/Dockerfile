ARG BUILDER_BASE_PLATFORM=linux/arm/v7
FROM --platform=${BUILDER_BASE_PLATFORM} debian:trixie AS builder

# Bootstrap the Raspberry Pi Foundation + Raspbian apt sources so the
# Qt 5 cross-build sysroot can pull libraspberrypi0 / libraspberrypi-bin
# (legacy Broadcom userland headers + libs that Qt 5 webview links
# against). Same approach as docker/Dockerfile.base.j2 — the .deb
# keyring packages bundle trixie-policy-compliant key bindings, so we
# extract them with dpkg-deb -x to avoid pulling in gnupg. Downloads
# are HTTPS-only and SHA256-pinned because the keyrings are the trust
# anchor for everything fetched after them.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    mkdir -p /tmp/keyrings && \
    curl --proto '=https' --tlsv1.2 -fsSL -o /tmp/keyrings/raspberrypi-archive-keyring.deb \
        https://archive.raspberrypi.org/debian/pool/main/r/raspberrypi-archive-keyring/raspberrypi-archive-keyring_2025.1+rpt1_all.deb && \
    curl --proto '=https' --tlsv1.2 -fsSL -o /tmp/keyrings/raspbian-archive-keyring.deb \
        https://archive.raspbian.org/raspbian/pool/main/r/raspbian-archive-keyring/raspbian-archive-keyring_20120528.4_all.deb && \
    echo '2e727149d7acb8cc7f604e66d0049161039c8aa1eaf1175e54f9e69d963d60e4  /tmp/keyrings/raspberrypi-archive-keyring.deb' | sha256sum -c - && \
    echo 'eb2bc175ecfad128ece8222b42eefabd0a2846afd14f3af04364f4a047cbc88f  /tmp/keyrings/raspbian-archive-keyring.deb' | sha256sum -c - && \
    dpkg-deb -x /tmp/keyrings/raspberrypi-archive-keyring.deb / && \
    dpkg-deb -x /tmp/keyrings/raspbian-archive-keyring.deb / && \
    rm -rf /tmp/keyrings && \
    printf 'Types: deb\nURIs: https://archive.raspberrypi.org/debian\nSuites: trixie\nComponents: main\nArchitectures: armhf\nSigned-By: /usr/share/keyrings/raspberrypi-archive-keyring.gpg\n' \
        > /etc/apt/sources.list.d/raspi.sources && \
    printf 'Types: deb\nURIs: https://archive.raspbian.org/raspbian\nSuites: trixie\nComponents: firmware\nArchitectures: armhf\nSigned-By: /usr/share/keyrings/raspbian-archive-keyring.gpg\n' \
        > /etc/apt/sources.list.d/raspbian-firmware.sources

# There are likely a large number of dependencies that can be stripped out here
# depending on your needs (and probably in general). My primary objective was just
# to make things work.
RUN apt-get update && \
    apt-get install -y \
        apt-utils \
        firebird-dev \
        freetds-dev \
        gstreamer1.0-tools \
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

FROM debian:trixie

# This list can most likely be slimmed down *a lot* but that's for another day.
RUN apt-get update && \
    apt-get -y install \
        bison \
        build-essential \
        ccache \
        cowsay \
        curl \
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
        libtiff6 \
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
        rsync \
        ruby \
        subversion \
        wget \
        make && \
    apt-get clean

# Install pyenv
RUN curl https://pyenv.run | bash && \
    echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bashrc && \
    echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.bashrc && \
    echo 'eval "$(pyenv init -)"' >> ~/.bashrc

# Install Python 2.7.18. This is needed to compile hthe QTWebEngine.
RUN /root/.pyenv/bin/pyenv install 2.7.18 && \
    /root/.pyenv/bin/pyenv global 2.7.18

WORKDIR /build

RUN wget -q https://raw.githubusercontent.com/riscv/riscv-poky/master/scripts/sysroot-relativelinks.py \
        -O /usr/local/bin/sysroot-relativelinks.py && \
    chmod +x /usr/local/bin/sysroot-relativelinks.py

RUN mkdir -p /sysroot/usr /sysroot/opt /sysroot/lib
COPY --from=builder /lib/ /sysroot/lib/
COPY --from=builder /usr/include/ /sysroot/usr/include/
COPY --from=builder /usr/lib/ /sysroot/usr/lib/

ENV BUILD_WEBVIEW=1
ENV CCACHE_MAXSIZE=10G
ENV CCACHE_DIR=/src/ccache
ARG GIT_HASH=0
ENV GIT_HASH=$GIT_HASH

COPY build_qt5.sh /usr/local/bin/
