FROM ubuntu:18.04
MAINTAINER Viktor Petersson <vpetersson@screenly.io>

RUN apt-get update && apt-get -y install \
        build-essential \
        curl \
        firefox \
        ffmpeg \
        git-core \
        libffi-dev \
        libsdl2-2.0-0 \
        libssl-dev \
        lsb-release \
        mplayer \
        net-tools \
        net-tools \
        procps \
        python-dev \
        python-gobject \
        python-gobject-2 \
        python-netifaces \
        python-pil \
        python-setuptools \
        python-simplejson \
        sqlite3 \
        xvfb \
    && \
    apt-get clean

# Gecko Driver
ENV GECKODRIVER_VERSION 0.24.0
RUN curl -L -o /tmp/geckodriver.tar.gz https://github.com/mozilla/geckodriver/releases/download/v$GECKODRIVER_VERSION/geckodriver-v$GECKODRIVER_VERSION-linux64.tar.gz \
  && rm -rf /opt/geckodriver \
  && tar -C /opt -zxf /tmp/geckodriver.tar.gz \
  && rm /tmp/geckodriver.tar.gz \
  && mv /opt/geckodriver /opt/geckodriver-$GECKODRIVER_VERSION \
  && chmod 755 /opt/geckodriver-$GECKODRIVER_VERSION \
  && ln -fs /opt/geckodriver-$GECKODRIVER_VERSION /usr/bin/geckodriver \
  && ln -fs /opt/geckodriver-$GECKODRIVER_VERSION /usr/bin/wires

# Install Python requirements
ADD requirements.txt /tmp/requirements.txt
ADD requirements.dev.txt /tmp/requirements.dev.txt
RUN curl -s https://bootstrap.pypa.io/get-pip.py | python && \
    pip install -r /tmp/requirements.txt && \
    pip install -r /tmp/requirements.dev.txt

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
