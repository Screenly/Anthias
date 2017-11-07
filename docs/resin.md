# Install Screenly OSE using Resin.io

* Setup Resin.io account https://docs.resin.io/raspberrypi3/nodejs/getting-started/#account-setup
* Create application https://docs.resin.io/raspberrypi3/nodejs/getting-started/#create-an-application
* Download resinOS https://docs.resin.io/raspberrypi3/nodejs/getting-started/#create-an-application
* Create a bootable SD card https://docs.resin.io/raspberrypi3/nodejs/getting-started/#create-a-bootable-sd-card
* Provision your Raspberry Pi https://docs.resin.io/raspberrypi3/nodejs/getting-started/#create-a-bootable-sd-card
* Clone the Screenly OSE repository (`git clone https://github.com/Screenly/screenly-ose.git`)
* Switch to the `master` branch `git checkout master`
* Add the resin git remote endpoint: `git remote add resin <USERNAME>@git.resin.io:<USERNAME>/<APPNAME>.git`
* Run the `git push` command: `git push resin master`
* Set the value to 64 for RESIN_HOST_CONFIG_gpu_mem in the Device Configuration panel