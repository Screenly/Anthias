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
  `balena login`.  You'll be prompted to select a login method. Select
  **Web authorization**, which is the recommended way to login. The browser will
  open a new tab. Click **Authorize** to proceed.
- Install balenaEtcher on your computer. You can download it from
  [here](https://etcher.balena.io/).

## Create and configure a new fleet

Open your browser and go to https://dashboard.balena-cloud.com. Login to your
balenaCloud account if you haven't already. You should be redirected to the
dashboard.

![balena-ss-01](/docs/images/balena-deployment-01-dashboard.png)

Click on the `Create fleet` button. Give your fleet a name and select the
appropriate device type. Click on the `Create new fleet` button. You should be
redirected to the fleet's summary page.

![balena-ss-02](/docs/images/balena-deployment-02-create-fleet.png)

![balena-ss-03](/docs/images/balena-deployment-03-fleet-summary-page.png)

We'll be doing the initial fleet configuration via CLI. Open your terminal and
run the following commands:

```bash
$ balena env add BALENA_HOST_CONFIG_gpu_mem $GPU_MEM_VALUE --fleet $FLEET_NAME
$ balena env add BALENA_HOST_CONFIG_dtoverlay vc4-kms-v3d --fleet $FLEET_NAME
```

If your display does have overscan issues like having a black border around the
screen, you can disable overscan by running the following command:

```bash
$ balena env add BALENA_HOST_CONFIG_disable_overscan 1 --fleet $FLEET_NAME
```

Replace `$GPU_MEM_VALUE` with the GPU memory value you want to use, as long as
it's less than the total memory of your device. For example, for a 4GB Raspberry
Pi 4, you can use `256` as the GPU memory value. Having insufficient GPU memory
might cause video playback issues.

You can confirm that the changes went through by running the following command:

```bash
balena envs --fleet $FLEET_NAME --config
```

Here's a sample output:

```
ID      NAME                           VALUE        FLEET
1979572 BALENA_HOST_CONFIG_dtoverlay    vc4-kms-v3d  gh_nicomiguelino/anthias-pi4
1979571 BALENA_HOST_CONFIG_gpu_mem      1024         gh_nicomiguelino/anthias-pi4
```

> [!TIP]
> Alternatively, you can check the releases page of that fleet and look for the
> `BALENA_HOST_CONFIG_gpu_mem` and `BALENA_HOST_CONFIG_dtoverlay` variables.

![balena-ss-04](/docs/images/balena-deployment-04-fleet-config-page.png)

## Deploy changes to the fleet

Before proceeding, make sure that you have logged in by running `balena login`.
You can verify if you're logged in by running `balena whoami`.

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
```

`$BOARD_TYPE` could be any one of the following &mdash; `pi1`, `pi2`, `pi3`, `pi4`.
You can run `./bin/deploy_to_balena.sh --help` for details.

Running the command above will pull the latest Docker images from Docker Hub and
push them to your balenaCloud account. It will also create a new release and
deploy it to your fleet.

If you want to deploy your local changes, run the following command instead:

```bash
# Take note of the --dev flag.
$ ./bin/deploy_to_balena.sh \
    --board $BOARD_TYPE \
    --fleet $FLEET_NAME \
    --dev
```

You can also includa a `--shm-size` flag to specify the shared memory size, which defaults
to `256mb`. For example:

```bash
$ ./bin/deploy_to_balena.sh \
    --board $BOARD_TYPE \
    --fleet $FLEET_NAME \
    --shm-size 512mb
```

It would take a while for the deployment to finish. Once it's done, you should
see the new release in the fleet's summary page. You can now add your devices to
the fleet and they should be able to download the new release.

![balena-ss-05](/docs/images/balena-deployment-05-term-deployment-successful.png)

![balena-ss-06](/docs/images/balena-deployment-06-fleet-releases-page.png)

## Add a new device to the fleet

Insert a microSD card to your computer.

Open your browser and go to https://dashboard.balena-cloud.com. You should be
redirected to your balenaCloud dashboard. Click on the fleet you created earlier.

Click "Add device" on the fleet's summary page. The "Add new device" page should
appear. Leave the default values unchanged. Click "Flash" when ready.

![balena-ss-07](/docs/images/balena-deployment-07-add-device.png)

A new browser tab will open prompting you to open balenaEtcher. Click "Open" to
proceed. Select the microSD card you inserted earlier and click "Flash". Wait for
the flashing process to finish.

![balena-ss-08](/docs/images/balena-deployment-08-etcher.png)

Remove the microSD card from your computer and insert it to your device. Power
on the device and wait for it to appear on the fleet's summary page. Once it
appears, click on the device's name to go to its summary page. Be patient while
balenaCloud downloads the Docker images and starts the containers. It might take
a while for the device to appear online.

![balena-ss-09](/docs/images/balena-deployment-09-device-list.png)

![balena-ss-10](/docs/images/balena-deployment-10-downloading-images.png)

Once done, the display should show the Anthias splash screen. You can now
add assets via the web interface.
