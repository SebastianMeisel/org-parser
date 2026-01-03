# Containerized Org Viewer (podman / docker)

## Files in this bundle
- Containerfile
- compose.yml
- entrypoint.sh
- requirements.txt
- .dockerignore

## Quick start (podman-compose)
1) Put these files into your repo root (where webapp.py lives).
2) Create a certs directory and place your TLS cert/key there:

   certs/tls.crt
   certs/tls.key

   (Or change CERT_FILE/KEY_FILE in compose.yml)

3) Run:

   podman-compose up -d --build

## Quick start (docker compose)
   docker compose -f compose.yml up -d --build

## Notes (Fedora / SELinux)
If you get permission errors on volume mounts, add :Z to the volume lines, e.g.:

  - ./org:/app/org:ro,Z

or set an SELinux label policy appropriate for your host.
