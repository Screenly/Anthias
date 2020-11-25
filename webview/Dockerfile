FROM balenalib/rpi-raspbian:buster

RUN echo "deb-src http://archive.raspberrypi.org/debian stretch main ui" >> /etc/apt/sources.list.d/raspi.list
RUN echo "deb-src http://archive.raspbian.org/raspbian stretch main contrib non-free rpi firmware" >> /etc/apt/sources.list

RUN apt-get update && \
    apt-get -y install --no-install-recommends \
        build-essential \
        freetds-dev \
        g++ \
        git \
        libgbm-dev \
        libgles2-mesa-dev \
        libinput-dev \
        libqt5gui5 \
        libqt5webkit5-dev \
        libqt5x11extras5-dev \
        libraspberrypi-dev \
        libraspberrypi0 \
        libts-dev \
        libudev-dev \
        libxcb-xinerama0 \
        libxcb-xinerama0-dev \
        lsb-release \
        make && \
    apt-get build-dep libqt5gui5 && \
    apt-get clean

WORKDIR /build

COPY build_qtbase.sh /usr/local/bin/
CMD /usr/local/bin/build_qtbase.sh
