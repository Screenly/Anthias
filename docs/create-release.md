# Creating a new release

* Run through the [QA Checklist](*https://www.forgett.com/checklist/1789089623)
* Create a new release and link to the corresponding sprint
* Create a release tag in `git`:

```
$ git tag -a vX.Y.Z -m "Release name"
$ git push origin vX.Y.Z
```

* Create a new disk image by triggering the [Balena Disk Image](https://github.com/Screenly/screenly-ose/actions/workflows/build-balena-disk-image.yaml) job
* Download the resulting images from the job
* Unzip the content from the zip file
* Upload the new disk image to the release above
