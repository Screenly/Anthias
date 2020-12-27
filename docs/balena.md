# Install Screenly OSE using Balena.io

## tl;dr instructions

* Create a [Balena](https://www.balena.io) account
* [Create application](https://docs.balena.io/raspberrypi3/nodejs/getting-started/#create-an-application)
* Download balenaOS
* Flash out the disk image
* Boot the device


To use Balena and any other Pi board, you will need to use the [Balena CLI](https://github.com/balena-io/balena-cli).

Once you have Balena CLI installed, run the following commands:

```
$ git clone git@github.com:Screenly/screenly-ose.git
$ cd screenly-ose
$ balena login
[...]
```

To deploy, you need to use the following commands:
```
$ ./bin/set_balena_variables.sh
$ balena deploy $NAME_OF_YOUR_APP
```

Note that you need to re-run these commands every time you deploy.

If you're having playback issues, you may need to manually apply [these](https://github.com/Screenly/screenly-ose/blob/master/balena.yml#L13-L16) settings to your device.

## Longer instructions

For more detailed instructions, including a screencast, check out the blog post [Deploy a digital signage application with Screenly OSE and balena.io](https://resin.io/blog/deploy-a-digital-signage-application-with-screenly-and-resin/).
