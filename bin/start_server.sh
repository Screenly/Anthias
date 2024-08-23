#!/bin/bash

ENVIRONMENT=${ENVIRONMENT:-production}

mkdir -p \
    /data/.config \
    /data/.screenly \
    /data/screenly_assets

cp -n /usr/src/app/ansible/roles/screenly/files/screenly.conf /data/.screenly/screenly.conf
cp -n /usr/src/app/ansible/roles/screenly/files/default_assets.yml /data/.screenly/default_assets.yml
cp -n /usr/src/app/ansible/roles/screenly/files/screenly.db /data/.screenly/screenly.db

if [ -n "${OVERWRITE_CONFIG}" ]; then
    echo "Requested to overwrite Anthias config file."
    cp /usr/src/app/ansible/roles/screenly/files/screenly.conf "/data/.screenly/screenly.conf"
fi

# Set management page's user and password from environment variables,
# but only if both of them are provided. Can have empty values provided.
if [ -n "${MANAGEMENT_USER+x}" ] && [ -n "${MANAGEMENT_PASSWORD+x}" ]; then
    sed -i -e "s/^user=.*/user=${MANAGEMENT_USER}/" -e "s/^password=.*/password=${MANAGEMENT_PASSWORD}/" /data/.screenly/screenly.conf
fi

echo "Running migration..."
python3 ./bin/migrate.py

./manage.py initialize_assets
./manage.py makemigrations
./manage.py migrate --fake-initial

if [[ "$ENVIRONMENT" == "development" ]]; then
    echo "Starting Django development server..."
    ./manage.py runserver 0.0.0.0:8080
else
    echo "Generating Django static files..."
    ./manage.py collectstatic --clear --noinput
    echo "Starting Gunicorn..."
    python3 run_gunicorn.py
fi
