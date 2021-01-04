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

To deploy, you need to use the following command:
```
$ ./bin/deploy_to_balena.sh
```

Note that you need to re-run this command every time you deploy.
