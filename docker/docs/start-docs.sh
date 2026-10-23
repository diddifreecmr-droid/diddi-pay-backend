#!/bin/sh
set -eu

if [ "${DOCS_PUBLIC_ENABLED:-false}" = "true" ]; then
    config=/opt/diddipay-docs/nginx-enabled.conf
else
    config=/opt/diddipay-docs/nginx-disabled.conf
fi

cp "$config" /etc/nginx/conf.d/default.conf
exec nginx -g 'daemon off;'
