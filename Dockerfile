FROM debian:wheezy
MAINTAINER Viktor Petersson <vpetersson@wireload.net>

RUN apt-get update && apt-get -y upgrade && apt-get -y install git-core net-tools python-pip python-netifaces python-simplejson python-imaging python-dev sqlite3 && apt-get clean

# Install Python requirements
ADD requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# Create runtime user
RUN useradd pi

# Install config file and file structure
RUN mkdir -p /home/pi/.screenly /home/pi/screenly /home/pi/screenly_assets
ADD misc/screenly.conf /home/pi/.screenly/screenly.conf
RUN chown -R pi:pi /home/pi

USER pi
WORKDIR /home/pi/screenly

EXPOSE 8080
VOLUME /home/pi/screenly

CMD python server.py
