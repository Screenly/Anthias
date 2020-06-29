# Screenly OSE - Digital Signage for the Raspberry Pi

To learn more about Wireload's Screenly, the original project this fork is based on, please visit the official website at [ScreenlyApp.com](http://www.screenlyapp.com). On the official site, you'll find the complete installation instructions.

## This Fork

It was found that the original Screenly missed many useful features including time and pattern based, reoccuring, schedules. These features are being worked on so that a full featured, standalone, digital signage package can be run on the Raspberry Pi for free.

Development is happening in the 'development' branch (and sub branches). Commits will be made to master once I think things are stable and functional.

You'll want to use Raspbian Buster as your underlying OS. Stretch should work but Jessie and earlier OS's will most likely not work due to a number of requirements. We've tested the Development branch on Raspberry Pi 2 and 3.

Installation instructions are available at [http://jameskirsop.github.io/pisign/](http://jameskirsop.github.io/pisign/)

### Running the Unit Tests

    nosetests --with-doctest
