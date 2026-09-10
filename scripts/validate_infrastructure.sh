#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d)
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT INT TERM

for command_name in nginx openssl systemd-analyze; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Infrastructure validation tool is missing: $command_name" >&2
    exit 2
  fi
done

mkdir -p \
  "$TMP/nginx-default/conf.d" \
  "$TMP/nginx-https/conf.d" \
  "$TMP/systemd"

# The Docker config normally resolves `api` through Compose DNS. Use localhost
# only for an isolated syntax check in a temporary nginx prefix.
sed \
  -e 's/server api:8080;/server 127.0.0.1:8080;/' \
  -e 's/listen 80;/listen 18080;/' \
  "$ROOT/deploy/nginx/default.conf" >"$TMP/nginx-default/conf.d/default.conf"
cat >"$TMP/nginx-default/nginx.conf" <<NGINX
pid $TMP/nginx-default/nginx.pid;
error_log $TMP/nginx-default/error.log;
events {}
http {
  access_log $TMP/nginx-default/access.log;
  include $TMP/nginx-default/conf.d/*.conf;
}
NGINX
nginx -t -p "$TMP/nginx-default" -c "$TMP/nginx-default/nginx.conf"

# Validate the TLS example with an ephemeral self-signed certificate. The
# generated key never leaves TMP and is removed by the trap.
openssl req -x509 -newkey rsa:2048 -nodes \
  -subj '/CN=panel.example.com' \
  -keyout "$TMP/nginx-https/key.pem" \
  -out "$TMP/nginx-https/cert.pem" \
  -days 1 >/dev/null 2>&1
sed \
  -e "s#/etc/letsencrypt/live/panel.example.com/fullchain.pem#$TMP/nginx-https/cert.pem#" \
  -e "s#/etc/letsencrypt/live/panel.example.com/privkey.pem#$TMP/nginx-https/key.pem#" \
  -e 's/listen 80;/listen 18080;/' \
  -e 's/listen 443 ssl;/listen 18443 ssl;/' \
  "$ROOT/deploy/nginx/teleflow-https.example.conf" \
  >"$TMP/nginx-https/conf.d/default.conf"
cat >"$TMP/nginx-https/nginx.conf" <<NGINX
pid $TMP/nginx-https/nginx.pid;
error_log $TMP/nginx-https/error.log;
events {}
http {
  access_log $TMP/nginx-https/access.log;
  include $TMP/nginx-https/conf.d/*.conf;
}
NGINX
nginx -t -p "$TMP/nginx-https" -c "$TMP/nginx-https/nginx.conf"

# systemd-analyze should validate unit structure rather than host-specific
# users, executable paths or writable directories. Substitute only those
# deployment-specific values in temporary copies.
for source_unit in "$ROOT"/deploy/systemd/*.service; do
  target_unit="$TMP/systemd/$(basename "$source_unit")"
  sed \
    -e 's/^User=.*/User=root/' \
    -e 's/^Group=.*/Group=root/' \
    -e "s#^WorkingDirectory=.*#WorkingDirectory=$ROOT#" \
    -e 's#^EnvironmentFile=.*#EnvironmentFile=-/dev/null#' \
    -e 's#^ExecStart=.*#ExecStart=/bin/true#' \
    -e 's#^ReadWritePaths=.*#ReadWritePaths=/tmp#' \
    "$source_unit" >"$target_unit"
done
systemd-analyze verify "$TMP"/systemd/*.service

echo "TeleFlow infrastructure syntax validation passed"
