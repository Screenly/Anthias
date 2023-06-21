# Deploying on a Balena fleet

You can use the Balena disk images provided in the releases page to install
Anthias in your device. However, if you want more control and transparency over
the Balena deployment, follow through the steps below to deploy Anthias on your
Balena fleet.

## Getting started

- Create a [balenaCloud](https://www.balena.io/cloud) account if you don't have
  one already.
- Install the [balena CLI](https://docs.balena.io/reference/balena-cli/) on your
  computer. It's recommended to install it on a Linux machine but it should work
  on Windows and macOS as well. If you're on Windows, you can use the Windows
  Subsystem for Linux (WSL) to install the CLI. Click
  [here](https://github.com/balena-io/balena-cli/blob/master/INSTALL.md) for the
  installation instructions.
- On your terminal, login to your balenaCloud account using the CLI by running
  `balena login`.
- Generate an API key for your balenaCloud account by going to your account
  settings page and clicking on the `Create API Key` button. Copy the API key
  and save it somewhere safe. You'll need it later. Alternatively, you can run
  `balena api-key generate <name>` to generate an API key using the CLI.
- Install balenaEtcher on your computer. You can download it from
  [here](https://etcher.balena.io/).

## Create and configure a new fleet

Open your browser and go to https://dashboard.balena-cloud.com. You should be
redirected to your balenaCloud dashboard.

Click on the `Create fleet` button. Give your fleet a name and select the
appropriate device type. Click on the `Create new fleet` button. You should be
redirected to the fleet's summary page.

We'll be doing the initial fleet configuration via CLI. Open your terminal and
run the following commands:

```bash
$ balena env add RESIN_HOST_CONFIG_gpu_mem $GPU_MEM_VALUE --fleet $FLEET_NAME

# Run the command below only if you're using a Raspberry Pi 4, as it uses
# VLC for video playback.
$ balena env add RESIN_HOST_CONFIG_dtoverlay vc4-fkms-v3d --fleet $FLEET_NAME
```

Replace `$GPU_MEM_VALUE` with the GPU memory value you want to use, as long as
it's less than the total memory of your device. For example, for a 4GB Raspberry
Pi 4, you can use `256` as the GPU memory value. Having insufficient GPU memory
might cause video playback issues.

## Deploy changes to the fleet

Open your terminal and clone the Anthias repository if you haven't already:

```bash
$ cd $WORKSPACE_DIRECTORY
$ git clone git@github.com:Screenly/Anthias.git
$ cd Anthias/
```

Run the following command:

```bash
$ ./bin/deploy_to_balena.sh \
    --board $BOARD_TYPE \
    --fleet $FLEET_NAME \
    --api-key $API_KEY
```

Running the command above will pull the latest Docker images from Docker Hub and
push them to your balenaCloud account. It will also create a new release and
deploy it to your fleet.

Deploying local changes to the fleet isn't supported by the script at the moment.

It would take a while for the deployment to finish. Once it's done, you should
see the new release in the fleet's summary page. You can now add your devices to
the fleet and they should be able to download the new release.

## Add a new device to the fleet

Insert a microSD card to your computer.

Open your browser and go to https://dashboard.balena-cloud.com. You should be
redirected to your balenaCloud dashboard. Click on the fleet you created earlier.

Click "Add device" on the fleet's summary page. The "Add new device" page should
appear. Leave the default values unchanged. Click "Flash" when ready.

A new browser tab will open prompting you to open balenaEtcher. Click "Open" to
proceed. Select the microSD card you inserted earlier and click "Flash". Wait for
the flashing process to finish.

Remove the microSD card from your computer and insert it to your device. Power
on the device and wait for it to appear on the fleet's summary page. Once it
appears, click on the device's name to go to its summary page. Be patient while
balenaCloud downloads the Docker images and starts the containers. It might take
a while for the device to appear online.

Once done, the display should show the Anthias splash screen. You can now
add assets via the web interface.
