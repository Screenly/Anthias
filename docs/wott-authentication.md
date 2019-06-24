# WoTT credentials based authentication to restrict access to the web GUI

Access to Screenly's web configuration interface (management page) can be restricted using HTTP Basic authentication; denying login access without a correct username and password. Here we show you how to use WoTT to add and manage those credentials.
**Note:** you will need to be using the development branch of Screenly OSE. Install it with:
```
$ bash <(curl -sL https://www.screenly.io/install-ose.sh)
```

Sister documentation [here](https://github.com/WoTTsecurity/agent/tree/master/docs/examples/screenly). Denotes use-case where Screenly OSE authentication can be managed by WoTT credentials.

## Installing WoTT agent onto Screenly OSE 

First you need to install WoTT's Agent onto your Screenly OSE device. To do this, you need to run the Screenly OSE installer:

```
$ ./screenly/.bin/run-upgrade.sh
```

and select the WoTT agent from the installation options.

Once WoTT is installed, you will be prompted to enter your WoTT device ID and claim token on the [WoTT dash](https://dash.wott.io). Register for the WoTT dash if you have not done so. If you cannot readily access your WoTT details, you can run the following:
```
$ wott-agent whoami
$ wott-agent claim-token
```
on the Screenly OSE device and enter these details into the 'Claim Device' segment of the dash.

![claim device](https://github.com/Screenly/screenly-ose/blob/master/docs/images/claim-device.png)

After this, navigate to the main dashboard and select the Raspberry Pi. Add `screenly-pi` to Tags.

![tag](https://github.com/Screenly/screenly-ose/blob/master/docs/images/tag.png)

## Configuring WoTT credentials

With the agent set up, you now need to add the desired credentials for authentication of the Screenly management page. 
Navigate to the 'Credentials' page of the WoTT dash.

![credentials](https://github.com/Screenly/screenly-ose/blob/master/docs/images/credentials.png)

Add a new credential with the following fields:

```
Name = screenly
Key = login
Value = username:password
Owner = pi
Tags = screenly-pi
```

Some notes regarding the fields:

- Name is `screenly` as the config file calls the credential by this field. If changed, you will need to manually alter the screenly config file
-  The Key refers to the fact that this is a `login`. Value must be in the form `username:password` but you can change this to your own authentication values of choice
- Owner is the Linux user of the raspberry pi, so `pi` 
- Tags is how WoTT identifies the device it is downloading the credentials on. Make sure it matches the Tag you assigned the Pi earlier

Information from WoTT is fetched every 15 minutes, however you can force download it immediately by: 

```
$ sudo service wott-agent restart
```

To use the certificate, restart the Screenly server:

```
$ sudo service screenly-web restart
```

**Note:** it may take a few minutes for the certificate to download onto your device- especially if it is an older Pi.

## Enabling WoTT authentication on Screenly OSE

On Screenly OSE management page, navigate to Settings and scroll down to Authentication. Select WoTT.

![screenly wott](https://github.com/Screenly/screenly-ose/blob/master/docs/images/screenly-wott.png)

The next time you access the management page IP address via a browser, you will be asked to login with a username and password like so:

![login](https://github.com/Screenly/screenly-ose/blob/master/docs/images/screenly-chrome.png)

And that is WoTT authentication set up on Screenly OSE. If you want to turn this off or change authentication method, simply change the authentication settings on the Screenly OSE management page.

## Closing notes 

Screenly pushes performance to the limit. Older Pis will have slower performance, do not be surprised if installation of the development branch takes 30+ minutes or if the WoTT agent itself is installed slowly.

WoTT credential details are fetched every 15 minutes. Make sure to restart the agent if you want the data immediately.