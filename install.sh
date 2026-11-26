#!/usr/bin/env bash
# Singen mit anders – Einrichtung auf einem frischen Ubuntu-Server (24.04).
# Aufruf:  curl -fsSL https://raw.githubusercontent.com/<BENUTZER>/singen-mit-anders/main/install.sh | sudo bash -s <BENUTZER> <CHOR-PASSWORT>
set -euo pipefail
GHUSER="${1:?GitHub-Benutzername fehlt}"
PASS="${2:?Chor-Passwort fehlt}"
REPO="https://github.com/${GHUSER}/singen-mit-anders.git"
DIR=/opt/singen-mit-anders

echo "== Pakete installieren =="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 git curl debian-keyring debian-archive-keyring apt-transport-https >/dev/null
systemctl enable --now docker

echo "== Code holen =="
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q "$REPO" "$DIR"; fi
cd "$DIR"

echo "== App bauen und starten (dauert beim ersten Mal 5-10 Minuten) =="
mkdir -p data
docker compose up -d --build

echo "== https-Adresse und Passwort einrichten =="
IP=$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')
HOST="$(echo "$IP" | tr . -).sslip.io"
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
fi
HASH=$(caddy hash-password --plaintext "$PASS")
cat > /etc/caddy/Caddyfile <<EOF
$HOST {
  encode gzip
  basic_auth {
    chor $HASH
  }
  request_body {
    max_size 50MB
  }
  reverse_proxy 127.0.0.1:8000
}
EOF
systemctl reload caddy || systemctl restart caddy

echo
echo "=============================================================="
echo " Fertig!  „Singen mit anders“ ist erreichbar unter:"
echo "   https://$HOST"
echo " Benutzername: chor     Passwort: (das eben gewählte)"
echo "=============================================================="
