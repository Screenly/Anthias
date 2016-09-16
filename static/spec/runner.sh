#!/usr/bin/env bash
for i in {1..10}
do
    timeout 7 phantomjs static/spec/phantom-runner.js
    last=$?
    if [[ "$last" -ne "124" ]]
        then exit $last
    fi
    echo "timeout: retry"
done

echo 'max retries reached. fail!'
exit 1