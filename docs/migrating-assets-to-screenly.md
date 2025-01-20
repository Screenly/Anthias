# Migrating assets from Anthias to Screenly

> [!NOTE]
> This feature is only available in devices running Raspberry Pi OS at the moment.

To get started, SSH to your Raspberry Pi running Anthias. For instance:

```bash
$ ssh pi@raspberrypi
```

Go to the project root directory and create a Python virtual environment, if you haven't created one.

```bash
$ cd ~/screenly
$ python -m venv venv/
```

Activate the virtual environment. You need to do this everytime right before you run the script.

```bash
$ source ./venv/bin/activate
```

Install the dependencies required by the assets migration script.

```bash
$ pip install -r requirements/requirements.local.txt
```

Before running the script, you should prepare the following:
* Your Screenly API key
* Anthias username and password, if your device has basic authentication enabled

Run the assets migration script. Follow through the instructions & prompts carefully.

```bash
$ python tools/migrate_assets_to_screenly.py
```