[![Build Status](https://travis-ci.org/wireload/screenly-ose.svg?branch=master)](https://travis-ci.org/wireload/screenly-ose)
[![Coverage Status](https://coveralls.io/repos/wireload/screenly-ose/badge.svg?branch=master&service=github)](https://coveralls.io/github/wireload/screenly-ose?branch=master)

# Screenly OSE - Digital Signage for the Raspberry Pi

To learn more about Screenly, please visit the official website at [ScreenlyApp.com](http://www.screenlyapp.com). On the official site, you'll find the complete installation instructions, along with a live-demo of Screenly.

## Dockerized Development Environment

To simplify development of the server module of Screenly OSE, we've created a Docker container. This is intended to run on your local machine with the Screenly OSE repository mounted as a volume.

Assuming you're in the source code repository, simply run:

```
$ docker run --rm -ti \
  -p 8080:8080 \
  -v $(pwd):/home/pi/screenly \
  wireload/screenly-ose-server
```

## Api Docs

All routes will be written with the assumption that the root is http://<ip-address>:8080


### GET /api/assets

Accept: application/json
```
[
  {
    "asset_id": "793406aa1fd34b85aa82614004c0e63a",
    "mimetype": "webpage",
    "name": "Website",
    "end_date": "2017-09-01T02:05:00",
    "is_enabled": 1,
    "nocache": 0,
    "is_active": true,
    "uri": "https://docs.google.com/presentation/d/1MNihWh1AQrgp1_yKZ5qge_KIQ3YvdrGFo9oEgA2p6No/pub?start=true&loop=true&delayms=3000",
    "duration": "15",
    "play_order": 0,
    "start_date": "2016-02-10T03:05:00"
  },
  {
    "asset_id": "1c5cfa719d1f4a9abae16c983a18903b",
    "mimetype": "video",
    "name": "Web Video",
    "end_date": "2016-04-12T23:26:00",
    "is_enabled": 1,
    "nocache": 0,
    "is_active": true,
    "uri": "http://www.w3schools.com/html/mov_bbb.mp4",
    "duration": "10",
    "play_order": 1,
    "start_date": "2016-02-02T00:26:00"
  },
  {
    "asset_id": "9c41068f3b7e452baf4dc3f9b7906595",
    "mimetype": "image",
    "name": "Web Image",
    "end_date": "2016-06-23T04:22:00",
    "is_enabled": 1,
    "nocache": 0,
    "is_active": true,
    "uri": "http://cdn.countercurrentnews.com/wp-content/uploads/2016/01/anonymous-israel.jpg",
    "duration": "10",
    "play_order": 2,
    "start_date": "2016-02-01T05:22:00"
  }
]
```


### POST /api/assets
Yes, that is just a string of json not json itself it will be parsed on the other end

Content-Type: application/x-www-form-urlencoded
```
model:
"{
  "name": "Website",
  "mimetype": "webpage",
  "uri": "http://www.duckduckgo.com",
  "is_active": false,
  "start_date": "2016-02-02T00:33:00.000Z",
  "end_date": "2016-03-01T00:33:00.000Z",
  "duration": "10",
  "is_enabled": 0,
  "nocache": 0,
  "play_order": 0
}"
```

Best way to find what is being sent is to open up developer tools and watch
the network tab when on the web frontend



### GET /api/assets/:asset_id

Accept: application/json
```
{
  "asset_id": "793406aa1fd34b85aa82614004c0e63a",
  "mimetype": "webpage",
  "name": "Website",
  "end_date": "2017-09-01T02:05:00",
  "is_enabled": 1,
  "nocache": 0,
  "is_active": true,
  "uri": "https://docs.google.com/presentation/d/1MNihWh1AQrgp1_yKZ5qge_KIQ3YvdrGFo9oEgA2p6No/pub?start=true&loop=true&delayms=3000",
  "duration": "15",
  "play_order": 0,
  "start_date": "2016-02-10T03:05:00"
}
```

### PUT/POST /api/assets/:asset_id

Content-Type: application/json
```
model:
"{
  "name": "Website",
  "mimetype": "webpage",
  "uri": "http://www.duckduckgo.com",
  "is_active": false,
  "start_date": "2016-02-02T00:33:00.000Z",
  "end_date": "2016-03-01T00:33:00.000Z",
  "duration": "10",
  "is_enabled": 0,
  "nocache": 0,
  "play_order": 0
}"
```

### DELETE /api/assets/:asset_id

This route deletes asset with :asset_id


### POST /api/order

Content-Type: application/x-www-form-urlencoded
```
ids: "793406aa1fd34b85aa82614004c0e63a,1c5cfa719d1f4a9abae16c983a18903b,9c41068f3b7e452baf4dc3f9b7906595" // comma separated ids
```


## Disk Image Changelog

### 2015-02-25

 * Adds support for Raspberry Pi B+ V2.
 * Upgrades kernel and kernel modules.
 * Brings system packages up to date.
 * Various bug fixes.

### 2014-11-03

 * Adds a setting for time display in 24 or 12 hour formats.
 * System updates (including Bash and OpenSSL).
 * Solves a UTF8 bug ([#226](https://github.com/wireload/screenly-ose/issues/226)).
 * Various bug fixes.

### 2014-08-13

 * Adds support for Raspberry Pi Model B+.
 * Improves handling in `viewer.py` where the splash page is being displayed before `server.py` has been fully loaded.
 * Pulls in APT updates from Screenly's APT repository.
 * Other bug fixes up to commit 1946e252471fcf34c27903970fbde601189d65a5.

### 2014-07-17

 * Fixes issue with load screen failing to connect.
 * Adds support for video feeds ([#210](https://github.com/wireload/screenly-ose/issues/210)).
 * Resolves issue with assets not being added ([#209](https://github.com/wireload/screenly-ose/issues/209)).
 * Resolves issue with assets not moving to active properly ([#201](https://github.com/wireload/screenly-ose/issues/201)).
 * Pulls in APT updates from Screenly's APT repository.

### 2014-01-11

 * Upgrade kernel (3.10.25+) and firmware. Tracked in [this](https://github.com/wireload/rpi-firmware) fork.
 * Change and use Screenly's APT repository (apt.screenlyapp.com).
 * `apt-get upgrade` to the Screenly APT repository.
 * Update Screenly to latest version.
 * The disk image is available at [ScreenlyApp.com](http://www.screenlyapp.com).

## Running the Unit Tests

    nosetests --with-doctest
