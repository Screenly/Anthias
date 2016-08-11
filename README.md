[![Build Status](https://travis-ci.org/wireload/screenly-ose.svg?branch=master)](https://travis-ci.org/wireload/screenly-ose)
[![Coverage Status](https://coveralls.io/repos/wireload/screenly-ose/badge.svg?branch=master&service=github)](https://coveralls.io/github/wireload/screenly-ose?branch=master)

# Screenly OSE - Digital Signage for the Raspberry Pi

The tl;dr for installing Screenly OSE on [Raspbian](https://www.raspberrypi.org/downloads/raspbian/) Jessie is:

```
$ bash <(curl -sL https://www.screenlyapp.com/install-ose.sh)
```

(The installation will take 15-20 minutes or so depending on your connectivity and the speed of your SD card.)

To learn more about Screenly, please visit the official website at [ScreenlyApp.com](http://www.screenlyapp.com). On the official site, you'll find the complete installation instructions and disk images.

Quick links:

 * [FAQ](https://www.screenlyapp.com/faq/ose/)
 * [Screenly OSE Home](https://www.screenlyapp.com/ose/)
 * [Live Demo](http://ose.demo.screenlyapp.com/)
 * [QA Checklist](https://www.forgett.com/checklist/1789089623)

Screenly OSE works on all Raspberry Pi versions, including Raspberry Pi Zero and Raspberry Pi 3 Model B.

## Dockerized Development Environment

To simplify development of the server module of Screenly OSE, we've created a Docker container. This is intended to run on your local machine with the Screenly OSE repository mounted as a volume.

Assuming you're in the source code repository, simply run:

```
$ docker run --rm -ti \
  -p 8080:8080 \
  -v $(pwd):/home/pi/screenly \
  wireload/screenly-ose-server
```

## Running the Unit Tests

    nosetests --with-doctest
