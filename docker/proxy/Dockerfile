# Egress proxy: one per attempt. The agent container sits on an internal Docker
# network whose only way out is this squid, which admits the problem's
# allowed_domains (plus the agent's own API endpoint) and logs every request.
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends squid ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /var/log/bmbench \
 && chown proxy:proxy /var/log/bmbench

COPY squid.conf /etc/squid/squid.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

LABEL org.bmbench.role=proxy
EXPOSE 3128
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
