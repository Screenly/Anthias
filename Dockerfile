FROM debian:stretch
MAINTAINER Viktor Petersson <vpetersson@screenly.io>

RUN apt-get update && \
    apt-get -y install \
        build-essential \
        curl \
        ffmpeg \
        git-core \
        libffi-dev \
        libssl-dev \
        mplayer \
        net-tools \
        procps \
        python-dev \
        python-imaging \
        python-netifaces \
        python-simplejson \
        sqlite3 \
    && \
    apt-get clean

# Install Python requirements
ADD requirements.txt /tmp/requirements.txt
RUN curl -s https://bootstrap.pypa.io/get-pip.py | python && \
    pip install -r /tmp/requirements.txt

# Create runtime user
RUN useradd pi

# Install config file and file structure
RUN mkdir -p /home/pi/.screenly /home/pi/screenly /home/pi/screenly_assets
COPY ansible/roles/screenly/files/screenly.conf /home/pi/.screenly/screenly.conf

# Copy in code base
COPY . /home/pi/screenly
RUN chown -R pi:pi /home/pi

USER pi
WORKDIR /home/pi/screenly

EXPOSE 8080

CMD python server.py
