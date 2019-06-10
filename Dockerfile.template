FROM balenalib/%%BALENA_MACHINE_NAME%%-debian:jessie
MAINTAINER Pavel Safronov

RUN apt-get update && \
    apt-get -y install \
        build-essential \
        curl \
        git-core \
        libffi-dev \
        libssl-dev \
        matchbox \
        net-tools \
        nginx-light \
        omxplayer \
        psmisc \
        python-dev \
        python-imaging \
        python-netifaces \
        python-simplejson \
        libraspberrypi0 \
        lsb-release \
        ifupdown \
        sqlite3 \
        uzbl \
        x11-xserver-utils \
        xserver-xorg && \
    apt-get clean

# Install Python requirements
ADD requirements.txt /tmp/requirements.txt
RUN curl -s https://bootstrap.pypa.io/get-pip.py | python && \
    pip install --upgrade -r /tmp/requirements.txt

# Setup nginx
RUN rm /etc/nginx/sites-enabled/default
COPY ansible/roles/ssl/files/nginx_resin.conf /etc/nginx/sites-enabled/screenly.conf

COPY ansible/roles/screenly/files/gtkrc-2.0 /data/.gtkrc-2.0

COPY . /tmp/screenly

CMD ["bash", "chmod 777 /dev/vchiq"]
CMD ["bash", "/tmp/screenly/bin/start_balena.sh"]
