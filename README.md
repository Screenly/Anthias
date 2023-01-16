# Anthias - Digital Signage for the Raspberry Pi

![Anthias Logo](https://github.com/Screenly/Anthias/blob/master/static/img/dark.svg?raw=true  "Anthias Logo")

## Screenly OSE is now known as Anthias

To clear up confusion between Screenly and Anthias, we have decided to rename Screenly OSE to Anthias. More details can be found in [this blog post](https://www.screenly.io/blog/2022/12/06/screenly-ose-now-called-anthias/). The renaming process is now under way, and over the coming months, Anthias will receive a face lift and the love it deserves.


Want to help Anthias thrive? Support us using [GitHub Sponsor](https://github.com/sponsors/Screenly).

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Screenly/Anthias&type=Date)](https://star-history.com/#Screenly/Anthias&Date)


## Disk images

The quickest way to get started is to use [Raspberry Pi Imager](https://www.screenly.io/blog/2022/12/13/anthias-and-screenly-now-in-rpi-imager/), where you can find Anthias under `Other specific-purpose OS`. Alternatively, you can find our pre-built disk images (powered by [Balena Hub](https://hub.balena.io/)) [here](https://github.com/Screenly/Anthias/releases/latest/).

Do however note that that we are still in the process of knocking out some bugs. You can track the known issues [here](https://github.com/Screenly/Anthias/projects/8).

## Installing on Raspbian/Raspberry Pi OS

The tl;dr for on [Raspberry Pi OS](https://www.raspberrypi.com/software/) Bullseye Lite is:

```
$ bash <(curl -sL https://install-anthias.srly.io)
```

**This installation will take 15 minutes to several hours**, depending on variables such as:

 * The Raspberry Pi hardware version
 * The SD card
 * The internet connection

During ideal conditions (Raspberry Pi 3 Model B+, class 10 SD card and fast internet connection), the installation normally takes 15-30 minutes. On a Raspberry Pi Zero or Raspberry Pi Model B with a class 4 SD card, the installation will take hours. As such, it is usually a lot faster to use the provided disk images.

## Installing with Balena

While you can deploy to your own Balena fleet, the easiest way to deploy using [Balena OpenFleets](https://hub.balena.io/organizations/screenly_ose/fleets).

## Quick links

 * [Forum](https://forums.screenly.io/c/screenly-ose)
 * [Website](https://anthias.screenly.io) (hosted on GitHub and the source is available [here](https://github.com/Screenly/Anthias/tree/master/website))
 * [Live Demo](https://ose.demo.screenlyapp.com/)
 * [QA Checklist](https://github.com/Screenly/Anthias/blob/master/docs/qa-checklist.md)
 * [API Docs](https://ose.demo.screenlyapp.com/api/docs/)
 * [Developer Documentation](https://github.com/Screenly/Anthias/blob/master/docs/developer-documentation.md)

Anthias works on all Raspberry Pi versions, including Raspberry Pi Zero, Raspberry Pi 3 Model B, and Raspberry Pi 4 Model B.

## Dockerized Development Environment

To simplify development of the server module of Anthias, we've created a Docker container. This is intended to run on your local machine with the Anthias repository mounted as a volume.

Assuming you're in the source code repository, simply run:

```bash
$ docker-compose \
    -f docker-compose.dev.yml up
```

## Running the Unit Tests

Start the containers.

```bash
$ docker-compose \
    -f docker-compose.test.yml up -d
```

Run the unit tests.

```bash
$ docker-compose \
    -f docker-compose.test.yml \
    exec -T srly-ose-test bash ./bin/prepare_test_environment.sh -s
$ docker-compose \
    -f docker-compose.test.yml \
    exec -T srly-ose-test nosetests -v -a '!fixme'
```
