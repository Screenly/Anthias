# Install Screenly OSE using Resin.io

* Create a [Resin.io](https://www.resin.io) account
* [Create application](https://docs.resin.io/raspberrypi3/nodejs/getting-started/#create-an-application)
* Download resinOS
* Flash out the disk image
* Boot the device
* Clone the Screenly OSE repository:

```
$ git clone https://github.com/Screenly/screenly-ose.git
```

* Add the Resin git remote endpoint:

```
$ git remote add resin <USERNAME>@git.resin.io:<USERNAME>/<APPNAME>.git
```

* Push the code to Resin.io:

```
$ git push resin master
```

  * This will take some time, as all components are being installed
* Navigate to "Fleet Configuration" in the web interface and create a new configuration with the key `RESIN_HOST_CONFIG_gpu_mem` and value `64`.
