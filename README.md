[![Build Status](https://travis-ci.org/Screenly/screenly-ose.svg?branch=master)](https://travis-ci.org/Screenly/screenly-ose)
[![Codacy Badge](https://api.codacy.com/project/badge/Grade/5905ebcf4aab4220ad9fdf3fb679c49d)](https://www.codacy.com/app/vpetersson/screenly-ose?utm_source=github.com&amp;utm_medium=referral&amp;utm_content=Screenly/screenly-ose&amp;utm_campaign=Badge_Grade)

# Screenly OSE - Digital Signage for the Raspberry Pi

[Which Screenly version is right for me?](https://www.screenly.io/screenly-ose-vs-pro/)

**PLEASE NOTE:** Screenly OSE and the commercial version of Screenly (formerly known as Screenly Pro) are **two completely separate products**. They **do not share any code base and behave very differently** both with regards to management and performance. Hence do not use one to evaluate the other.

Want to help OSE thrive? Support us using [GitHub Sponsor](https://github.com/sponsors/Screenly).

## Disk images

The recommended installation method is to grab the latest disk image from [here](https://github.com/Screenly/screenly-ose/releases).

## Installing on Raspbian

The tl;dr for installing Screenly OSE on [Raspbian Lite](https://www.raspberrypi.org/downloads/raspbian/) is:

```
$ bash <(curl -sL https://www.screenly.io/install-ose.sh)
```

**This installation will take 15 minutes to several hours**, depending on variables such as:

 * The Raspberry Pi hardware version
 * The SD card
 * The internet connection

During ideal conditions (Raspberry Pi 3 Model B+, class 10 SD card and fast internet connection), the installation normally takes 15-30 minutes. On a Raspberry Pi Zero or Raspberry Pi Model B with a class 4 SD card, the installation will take hours. As such, it is usually a lot faster to use the provided disk images.

## Upgrading on Screenly OSE

The releases are based on the [Sprints](https://github.com/Screenly/screenly-ose/projects). At the end of each sprint, we merge the master branch (also known as the developer version), into the production branch and generate a new disk image.

Should you want to upgrade to the latest development version (for instance if you want to try a bug-fix), you can do this by simply re-running the installation script and select that you want to install the development version. Re-running the installation script should normally not take more than a few minutes (depending on how much changed).

To learn more about Screenly, please visit the official website at [Screenly.io](http://www.screenly.io).

[![An introduction to digital signage with Screenly OSE](http://img.youtube.com/vi/FQte5yP0azE/0.jpg)](http://www.youtube.com/watch?v=FQte5yP0azE)

Quick links:

 * [FAQ](https://support.screenly.io/hc/en-us/sections/202652366-Frequently-Asked-Questions-FAQ-)
 * [Screenly OSE Forum](https://forums.screenly.io/c/screenly-ose)
 * [Screenly OSE Home](https://www.screenly.io/ose/)
 * [Live Demo](http://ose.demo.screenlyapp.com/)
 * [QA Checklist](https://www.forgett.com/checklist/1789089623)
 * [API Docs](http://ose.demo.screenlyapp.com/api/docs/)

Screenly OSE works on all Raspberry Pi versions, including Raspberry Pi Zero and Raspberry Pi 3 Model B.

## Dockerized Development Environment

To simplify development of the server module of Screenly OSE, we've created a Docker container. This is intended to run on your local machine with the Screenly OSE repository mounted as a volume.

Assuming you're in the source code repository, simply run:

```
$ docker run --rm -it \
    --name=screenly-dev \
    -e 'LISTEN=0.0.0.0' \
    -p 8080:8080 \
    -v $(pwd):/home/pi/screenly \
    screenly/ose-dev-server
```

## Running the Unit Tests

    nosetests --with-doctest
