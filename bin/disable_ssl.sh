#!/bin/bash -e

# Switch the web interface back to the screen's own self-signed
# certificate, dropping an uploaded certificate or Let's Encrypt setup.
#
# HTTPS itself stays on: since Caddy became the front end of every
# install there is no plain-HTTP-only mode to return to. Port 80 still
# redirects the web UI to HTTPS and, for one more release, answers
# /api/* over plain HTTP.

exec "$(dirname "$0")/enable_ssl.sh"
