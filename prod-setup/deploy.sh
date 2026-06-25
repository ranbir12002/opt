#!/usr/bin/env bash
# deployment script for Plain-metal EC2 (Ubuntu 22.04 LTS / Debian)
set -e

APP_DIR="/opt/optificial"
ENV_FILE="/etc/optificial/backend.env"

echo "=== Updating System & Installing Prerequisites ==="
sudo apt-get update -y
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip nginx build-essential libpq-dev

echo "=== Creating App Directory Structure ==="
sudo mkdir -p "${APP_DIR}"
sudo chown -R $USER:$USER "${APP_DIR}"
sudo mkdir -p "/etc/optificial"
sudo touch "${ENV_FILE}"
sudo chmod 600 "${ENV_FILE}"

echo "=== Setting up Virtual Environments ==="
# 1. Backend + Agent libraries (requires shared scope)
python3.11 -m venv "${APP_DIR}/venv-backend"
source "${APP_DIR}/venv-backend/bin/activate"
pip install --upgrade pip
pip install -r "${APP_DIR}/Chatbox_mcp/backend/requirements.txt"
pip install -r "${APP_DIR}/svc-agent-invoice/requirements.txt"
pip install -r "${APP_DIR}/svc-agent-purchase-order/requirements.txt"
pip install -r "${APP_DIR}/svc-agent-schedule/requirements.txt"
pip install -r "${APP_DIR}/svc-agent-workorder/requirements.txt"
deactivate

# 2. Simpro MCP Server
python3.11 -m venv "${APP_DIR}/venv-simpro"
source "${APP_DIR}/venv-simpro/bin/activate"
pip install --upgrade pip
pip install -r "${APP_DIR}/mcp-simpro-server/requirements.txt"
deactivate

# 3. Extractor Service
python3.11 -m venv "${APP_DIR}/venv-extractor"
source "${APP_DIR}/venv-extractor/bin/activate"
pip install --upgrade pip
pip install -r "${APP_DIR}/svc-extractor/requirements.txt"
deactivate

echo "=== Configuring Systemd Services ==="
sudo cp "${APP_DIR}/prod-setup/services/"*.service /etc/systemd/system/
sudo systemctl daemon-reload

echo "=== Configuring Nginx ==="
sudo cp "${APP_DIR}/prod-setup/nginx.conf" /etc/nginx/sites-available/optificial-backend
sudo ln -sf /etc/nginx/sites-available/optificial-backend /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo systemctl restart nginx

echo "============================================="
echo " Deployment layout prepared successfully! "
echo " Please write your production variables to:  "
echo "   ${ENV_FILE}                              "
echo " Then start services with:                  "
echo "   sudo systemctl start optificial-backend  "
echo "   sudo systemctl start optificial-simpro   "
echo "   sudo systemctl start optificial-extractor"
echo "============================================="
