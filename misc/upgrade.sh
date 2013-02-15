#!/bin/bash

echo "Upgrading Screenly OSE..."

echo "Fetching the latest update..."
cd ~/screenly
git pull

echo "Ensuring all Python modules are installed"
sudo pip install -r requirements.txt -q

echo "Running migration..."
python misc/migrate.py

echo "Restarting app-server..."
sudo supervisorctl restart screenly


echo "Restarting viewer module..."
pkill -f "viewer.py"

echo "Done!"
