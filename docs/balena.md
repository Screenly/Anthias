# Install Screenly OSE using Balena.io

## tl;dr instructions

* Create a [balena.io](https://www.resin.io) account
* [Create application](https://docs.balena.io/raspberrypi3/nodejs/getting-started/#create-an-application)
* Download balenaOS
* Flash out the disk image
* Boot the device
* Clone the Screenly OSE repository:

```
$ git clone https://github.com/Screenly/screenly-ose.git
```

* Add the balena git remote endpoint:

```
$ git remote add balena <USERNAME>@git.resin.io:<USERNAME>/<APPNAME>.git
```

* Push the code to balena.io:

```
$ git push balena master
```

*(This will take some time, as all components are being installed)*

* Navigate to "Fleet Configuration" in the web interface and create a new configuration with the key `balena_HOST_CONFIG_gpu_mem` and the value `64`. If you're having issues with video playback performance, you may need to increase this to 192, or sometimes even 256.

## Longer instructions

For more detailed instructions, including a screencast, check out the blog post [Deploy a digital signage application with Screenly OSE and balena.io](https://resin.io/blog/deploy-a-digital-signage-application-with-screenly-and-resin/).
