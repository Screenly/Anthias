# Screenly OSE Disk Image steps

 * Create a new Debian 9 VM with decent resources
 * Run the following commands:

```
$ git clone git@github.com:Screenly/pi-gen.git
$ cd pi-gen
$ sudo ./build.sh
[ You will likely need to install some more dependencies reported by the tool ]
$ sudo chown -R $(whoami) deploy
$ cd deploy
$ for i in *.zip; do md5sum $i > $i.md5 && sha256sum $i > $i.sha256; done
```

When the process completes (this can take a 1-2h or more), the disk image along with the NOOBS images should be available in the `deploy` folder.
