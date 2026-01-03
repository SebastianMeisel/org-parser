#!/usr/bin/env sh
set -eu

WORKERS="${WORKERS:-4}"
BIND="${BIND:-0.0.0.0:5000}"

# Optional TLS: mount your certs into /certs and set CERT_FILE/KEY_FILE,
# or use the defaults below.
CERT_FILE="${CERT_FILE:-/certs/tls.crt}"
KEY_FILE="${KEY_FILE:-/certs/tls.key}"

# Gunicorn app import path
APP="${APP:-webapp:app}"

EXTRA_ARGS=""

if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
  EXTRA_ARGS="--certfile $CERT_FILE --keyfile $KEY_FILE"
  echo "[entrypoint] TLS enabled (cert: $CERT_FILE)"
else
  echo "[entrypoint] TLS disabled (no cert/key found)."
fi

exec gunicorn -w "$WORKERS" -b "$BIND" $EXTRA_ARGS "$APP"
