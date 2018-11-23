#!/bin/bash

# Add the sample assets
start_date=$(date '+%FT%H:%M:%SZ')
end_date=$(date '+%FT%H:%M:%SZ' -d '+6 years')

curl --header "Content-Type: application/json" \
  --request POST \
  --data "{
    \"name\": \"Screenly Weather Widget\",
    \"uri\": \"https://weather.srly.io\",
    \"mimetype\": \"webpage\",
    \"start_date\": \"$start_date\",
    \"end_date\": \"$end_date\",
    \"play_order\": 0,
    \"is_enabled\": 1,
    \"skip_asset_check\": 0
  }" \
  http://127.0.0.1:8080/api/v1.2/assets

curl --header "Content-Type: application/json" \
  --request POST \
  --data "{
    \"name\": \"Screenly Clock Widget\",
    \"uri\": \"https://clock.srly.io\",
    \"mimetype\": \"webpage\",
    \"start_date\": \"$start_date\",
    \"end_date\": \"$end_date\",
    \"play_order\": 1,
    \"is_enabled\": 1,
    \"skip_asset_check\": 0
  }" \
  http://127.0.0.1:8080/api/v1.2/assets

curl --header "Content-Type: application/json" \
  --request POST \
  --data "{
    \"name\": \"Hacker News\",
    \"uri\": \"https://news.ycombinator.com\",
    \"mimetype\": \"webpage\",
    \"start_date\": \"$start_date\",
    \"end_date\": \"$end_date\",
    \"play_order\": 2,
    \"is_enabled\": 1,
    \"skip_asset_check\": 0
  }" \
  http://127.0.0.1:8080/api/v1.2/assets
