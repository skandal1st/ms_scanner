#!/usr/bin/env bash
# Prepare an EMPTY Ubuntu host. Does not copy data, start apps or change DNS/SSH.
set -euo pipefail
if [[ $# != 1 || $(id -u) != 0 || $(hostname) != "$1" ]]; then
  echo 'Run as root with the verified target hostname as the sole argument.' >&2
  exit 2
fi
if command -v docker >/dev/null && [[ -n $(docker ps -aq) ]]; then
  echo 'Target already contains containers; inspect it before provisioning.' >&2
  exit 2
fi
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || { echo 'Ubuntu 24.04 required.' >&2; exit 2; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl rsync
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
if [[ ! -e /etc/docker/daemon.json ]]; then
  install -m 0755 -d /etc/docker
  cat > /etc/docker/daemon.json <<'EOF'
{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}
EOF
  systemctl restart docker
fi
systemctl enable docker
install -d -m 0700 /var/lib/skandata-migration
docker version --format '{{.Server.Version}}'
docker compose version
