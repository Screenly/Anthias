#!/bin/bash

SCREENLY="/home/pi/screenly"

echo "Upgrading Screenly OSE..."

echo "Ensuring proper permission is set..."
sudo chown -R pi:pi $SCREENLY
sudo chown -R pi:pi /home/pi/screenly_assets
sudo chown -R pi:pi /home/pi/.screenly

echo "Fetching the latest update..."
cd $SCREENLY
git pull

echo "Ensuring all Python modules are installed..."
sudo pip install -r $SCREENLY/requirements.txt -q

echo "Running migration..."
python $SCREENLY/misc/migrate.py

echo "Restarting app-server..."
sudo supervisorctl restart screenly


echo "Restarting viewer module..."
pkill -f "viewer.py"

echo "Done!"
