#!/bin/bash

ENVIRONMENT=${ENVIRONMENT:-production}

ls -l /etc/nginx/sites-available

ln -s /etc/nginx/sites-available/nginx.${ENVIRONMENT}.conf \
    /etc/nginx/sites-enabled/anthias.conf

nginx -g "daemon off;"
