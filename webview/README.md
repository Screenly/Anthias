# QT Dependencies

At the moment, this build will not work with cross-compiling, and needs to be done on a Raspberry Pi.

Start by building the base image:

```
$ docker build -t qt-builder .
[...]
```

With the base image done, you can build QT Base with the following command:

```
$ docker run --rm -ti \
    -v $(pwd)/build:/build -ti qt-builder
```

This will output the files in a folder called `build/` in the current directory.
