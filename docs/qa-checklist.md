# QA Checklist

Running unit tests is a good way to make sure that the code is working as expected. However, it's also important to do manual testing as some bugs may not be caught by unit tests. This document contains a list of things that you can do to test the application manually.

> [!NOTE]
> The list is not exhaustive, but you can use it as your guide when testing Anthias.

## General

1. Make sure that device is connected to the internet (e.g., via Ethernet).
2. Turn on the device and wait for the splash page to appear.
3. Make sure that the splash page is being displayed properly.
4. Use the provided IP address and make sure that entering it in a browser will redirect you to the web UI home page (the assets page).

## Content page

1. Add assets (image, video, or webpage) and make them active by toggling the switch.
2. Make sure that these assets are shown on the screen.
3. Disable the assets and make sure that the screen in standby mode, which means that it displays the Anthias standby page.
4. Change a duration for any asset and make sure that it is being displayed for the specified duration.
5. Change a the start and end dates and make sure that the asset is being displayed only during the specified period.
6. Turn on some assets and change their order (by dragging and dropping them). Make sure that the assets are being displayed in the correct order.
7. Try to change a name of any asset.
8. Turn on some assets. Click on Previous asset and on Next asset. Make sure that the screen displays the asset that comes before or after the current asset.
9. Click on the download button near any asset. The asset should be downloaded into your computer.

## Settings page

1. Go to the Settings page.
2. If desired, change the device's **Player name**.
3. Change a default value for the **Default duration** and upload any asset. Make sure that the duration value is correct.
4. Change a default value for the **Default streaming duration** and upload any stream. Make sure that the asset is being displayed for the specified duration.
5. Enable **Show splash screen** and reboot the device. Make sure that the splash screen is not being displayed upon boot.
6. Enable **Default assets** and make sure that the default assets are added to the list of active assets. Also make sure that the assets are being displayed on the screen.
7. Disable **Default assets** and make sure that the assets are deleted from the list of active assets. Also make sure that the assets are not being displayed on the screen.
8. Enable **Shuffle playlist**. Activate some assets and make sure that the assets are being displayed in random order.
9. Enable **Use 24-hour clock**. Go to the assets page and make sure that the time field uses correct format.
10. Enable any video asset with sounds and choose **HDMI** for the **Audio output**. Make sure that the sound works.
11. Enable any video file with sounds and choose **3.5mm** jack for the **Audio output**. Make sure that the sound works.
12. Choose any format for the **Date format**. Go to the assets page and make sure that the date field uses the correct format.
13. Enable authentication by selecting **Basic** from the **Authentication** dropdown. Reload page and make sure that you'll now be prompted to enter a username and a password.
14. Click the **Get Backup** button. Delete all assets. Click on **Upload and Recover** and make sure that the assets are restored.
15. Try to reboot or shutdown the device by clickin on the **Reboot** or **Shutdown** buttons, respectively. Make sure that the device does the corresponding action.
16. Go to the **System Info** page and make sure that all information are correct.
17. Make sure that all the footer links are being displayed correctly.
18. Go to the **Settings** page. Click on the **Reset Wi-Fi** and reboot your device. Make sure that the hotspot page displays.
19. Connect to the device by ssh. Run `./bin/enable_ssl.sh` script. Make sure the site URL uses SSL.
