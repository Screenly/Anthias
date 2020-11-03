# QA Checklist

(This is a partial list, as we lost the official list after Forgett closed down.)

1. Run device with an enabled ethernet and wait for the splash page.
2. Make sure that the splash page looks correct.
3. Use the provided link and make sure the link forwards to web UI.
4. Run device w/out ethernet and wait for the hotspot page.
5. Make sure that the hotspot page looks correct.
6. Connect to exists Screenly access point and wait for the splash page.
7. See step 2 and step 3


1. Run device with an enabled ethernet and wait for the splash page.
2. Make sure that the splash page looks correct.
3. Use the provided link and make sure the link forwards to web UI.
4. Run device w/out ethernet and wait for the hotspot page.
5. Make sure that the hotspot page looks correct.
6. Connect to exists Screenly access point and wait for the splash page.
7. See step 2 and step 3


## Content page

8. Add assets(image, video, webpage, stream) and make them as active.
9. Make sure that these assets are shown on a screen.
10. Disable the assets and make sure that the screen in standby mode.
11. Change a duration for any asset and make sure that the screen displays it during the correct time.
12. Change a start date and an end date and make sure that the asset displays in correct time.
13. Turn on some assets and change their order(with drag and drop). Make sure that the assets display with the correct order.
14. Try to change a name of any asset.
15. Turn on some assets. Click on Previous asset and on Next asset. Make sure that the screen changes assets on.
16. Click on a download button near any asset. The asset should be downloaded.


## Settings page

17. Setup a player name
18. Change a default value for the Default duration input and upload any asset. Make sure that the duration value is correct.
19. Change a default value for the Default streaming duration input and upload any stream. Make sure that the duration value is correct.
20. Turn on the Show splash screen and reboot device. Make sure that the splash screen is skipped.
21. Turn on the Default assets and make sure that the assets are added to content. Turn off the Default assets and make sure that the assets are deleted from content.
22. Turn on the Shuffle playlist. Activate some assets and make sure that the shuffle is working.
23. Turn on the Use 24-hour clock. Go to the content page and make sure that a time field uses correct format.
24. Activate any video file with sounds and choose HDMI for the Audion output. Make sure that the sound works.
25. Activate any video file with sounds and choose 3.5mm jack for the Audion output. Make sure that the sound works.
26. Choose any format for the Date format. Go to the content page and make sure that a date field uses correct format.
27. Turn on Basic auth. Reload page and make sure that works.
28. Click on the Get backup. Delete all assets. Click on Upload and Recover and make sure that the assets are restored.
29. Generate file for the USB assets. Put it and some assets on any usb stick. Turn on the stick to device. Make sure that the assets are displays on the content page. Turn on some assets and make sure that the assets display correct. Turn off the USB stick. Make sure that the assets are deleted from the content page and make sure that the screen doesn't display them.
30. Open the Upgrade Screenly and choose the next params:
*  Production
* Turn on a manage your network
* Turn off a full system upgrade

and then start the upgrade. Make sure that passed w/out errors.

31. Try to reboot device and Shutdown device from the settings page.
32. Go to the System Info page and make sure that all information are correct.
33. Make sure that all links in the footer is correct.
34. Go to the settings page. Click on the Reset wifi and reboot your device. Make sure that the hotspot page displays.

35. Connect to the device by ssh. Run `./bin/enable_ssl.sh` script. Make sure the site url uses the ssl.
