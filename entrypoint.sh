#!/bin/bash
# Rebuild the font cache with fonts from host OS 
fc-cache -f -v
exec "$@"
