#!/bin/sh
# ALLOWED_DOMAINS: whitespace/comma-separated domains. Each one admits the domain
# itself and every subdomain (squid dstdomain ".example.org"). The harness has
# already removed entries covered by a broader one, which squid would warn about.
set -eu
: > /etc/squid/allowed_domains.txt
for d in $(echo "${ALLOWED_DOMAINS:-}" | tr ',' ' '); do
  d="${d#.}"
  [ -n "$d" ] && echo ".$d" >> /etc/squid/allowed_domains.txt
done
if [ ! -s /etc/squid/allowed_domains.txt ]; then
  # squid refuses an empty dstdomain file; a reserved name matches nothing real.
  echo ".invalid" > /etc/squid/allowed_domains.txt
fi
exec squid -N -f /etc/squid/squid.conf
