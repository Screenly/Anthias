#!/usr/bin/env python3
# Vendored from
# https://github.com/riscv/riscv-poky/blob/cd3cba2bb49b9366ed10f2b81bffed071da5dc85/scripts/sysroot-relativelinks.py
# (Yocto/poky upstream). Pinned in-tree to avoid an unverified curl
# from raw.githubusercontent.com at Qt 5 webview build time. The script
# is short and stable; rewriting absolute symlinks under /sysroot to
# relative ones so cross-gcc resolves them inside the cross-compile
# sysroot rather than the build host's filesystem.
import sys
import os

# Take a sysroot directory and turn all the abolute symlinks and turn them into
# relative ones such that the sysroot is usable within another system.

if len(sys.argv) != 2:
    print("Usage is " + sys.argv[0] + "<directory>")
    sys.exit(1)

topdir = sys.argv[1]
topdir = os.path.abspath(topdir)

def handlelink(filep, subdir):
    link = os.readlink(filep)
    if link[0] != "/":
        return
    if link.startswith(topdir):
        return
    #print("Replacing %s with %s for %s" % (link, topdir+link, filep))
    print("Replacing %s with %s for %s" % (link, os.path.relpath(topdir+link, subdir), filep))
    os.unlink(filep)
    os.symlink(os.path.relpath(topdir+link, subdir), filep)

for subdir, dirs, files in os.walk(topdir):
    for f in dirs + files:
        filep = os.path.join(subdir, f)
        if os.path.islink(filep):
            #print("Considering %s" % filep)
            handlelink(filep, subdir)
