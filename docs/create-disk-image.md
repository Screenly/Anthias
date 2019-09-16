# Screenly OSE Disk Image steps

 * Create a new Debian 9 VM with decent resources
 * Run the following commands:

```
$ git clone git@github.com:Screenly/pi-gen.git
$ cd pi-gen
$ sudo ./build.sh
[ You will likely need to install some more dependencies reported by the tool ]
$ sudo ./package.sh
```

When the process completes (this can take a 1-2h or more), the disk image along with the NOOBS images should be available in the `deploy` folder.
