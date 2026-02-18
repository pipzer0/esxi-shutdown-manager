#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== ESXi Shutdown Manager - Deploy ==="

# 1) Install Docker if not present
if ! command -v docker &>/dev/null; then
    echo "[1/4] Installing Docker..."
    curl -fsSL https://raw.githubusercontent.com/pipzer0/init-docker/main/install_docker_ubuntu.sh -o /tmp/install_docker.sh
    sudo bash /tmp/install_docker.sh
    rm /tmp/install_docker.sh
else
    echo "[1/4] Docker already installed"
fi

# 2) Generate SSH key for ESXi access
mkdir -p ssh
if [ ! -f ssh/id_rsa ]; then
    echo "[2/4] Generating SSH key pair..."
    ssh-keygen -t rsa -b 4096 -f ssh/id_rsa -N "" -C "shutdown-manager"
    echo ""
    echo "============================================"
    echo "  ADD THIS PUBLIC KEY TO YOUR ESXI HOST:"
    echo "============================================"
    echo ""
    echo "  SSH into rack1.springfield and run:"
    echo ""
    echo "  cat >> /etc/ssh/keys-root/authorized_keys << 'EOF'"
    cat ssh/id_rsa.pub
    echo "EOF"
    echo ""
    echo "============================================"
    echo ""
    read -p "Press Enter after adding the key to ESXi..."
else
    echo "[2/4] SSH key already exists"
fi

# 3) Create data directory
mkdir -p data
echo "[3/4] Data directory ready"

# 4) Build and start
echo "[4/4] Building and starting container..."
docker compose up -d --build

echo ""
echo "=== Done! ==="
IP=$(hostname -I | awk '{print $1}')
echo "Access the dashboard at: http://${IP}:8080"
