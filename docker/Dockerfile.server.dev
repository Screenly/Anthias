FROM debian:stretch

RUN apt-get update && \
    apt-get -y install \
        build-essential \
        curl \
        ffmpeg \
        git-core \
        libffi-dev \
        libssl-dev \
        lsb-release \
        mplayer \
        net-tools \
        procps \
        python-dev \
        python-gobject \
        python-imaging \
        python-netifaces \
        python-simplejson \
        sqlite3 \
    && \
    apt-get clean

# Install Python requirements
ADD requirements/requirements.txt /tmp/requirements.txt
ADD requirements/requirements.dev.txt /tmp/requirements.dev.txt
RUN curl -s https://bootstrap.pypa.io/get-pip.py | python && \
    pip install -r /tmp/requirements.txt && \
    pip install -r /tmp/requirements.dev.txt

# Create runtime user
RUN useradd pi

# Install config file and file structure
RUN mkdir -p /home/pi/.screenly /home/pi/screenly /home/pi/screenly_assets
COPY ansible/roles/screenly/files/default_assets.yml /home/pi/.screenly/default_assets.yml
COPY ansible/roles/screenly/files/screenly.conf /home/pi/.screenly/screenly.conf
RUN chown -R pi:pi /home/pi/.screenly /home/pi/screenly_assets

USER pi
WORKDIR /home/pi/screenly

EXPOSE 8080

CMD python server.py
