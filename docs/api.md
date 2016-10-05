# API Documentation

**Note**: Work in progress.

All routes will be written with the assumption that the root is http://<ip-address>:8080


## GET /api/assets

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


## POST /api/assets

Yes, that is just a string of JSON not JSON itself it will be parsed on the other end.

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

Best way to find what is being sent is to open up developer tools and watch the network tab when on the web frontend


## GET /api/assets/:asset_id

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

## PUT/POST /api/assets/:asset_id

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

## DELETE /api/assets/:asset_id

This route deletes asset with :asset_id

## POST /api/assets/order

Content-Type: application/x-www-form-urlencoded
```
ids: "793406aa1fd34b85aa82614004c0e63a,1c5cfa719d1f4a9abae16c983a18903b,9c41068f3b7e452baf4dc3f9b7906595" // comma separated ids
```

