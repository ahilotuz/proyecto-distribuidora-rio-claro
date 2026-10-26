# Production deployment (Azure VM)

Target: Ubuntu 24.04 (Arm64) VM, Docker Compose, Caddy with automatic Let's Encrypt HTTPS.
Only ports 80/443 (and SSH 22) are public; Odoo and Postgres are never published.

## First deployment

```bash
git clone https://github.com/ahilotuz/proyecto-distribuidora-rio-claro.git ~/app
cd ~/app/deploy

# Secrets (never commit these two files)
cp odoo.prod.conf.example odoo.prod.conf
sed -i "s|CHANGE_ME_LONG_RANDOM|$(openssl rand -base64 24 | tr -d '/+=')|" odoo.prod.conf
cat > .env <<EOF
SITE_DOMAIN=rioclaro.ropech.com
ODOO_DB_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')
EOF
chmod 600 .env odoo.prod.conf
# The Odoo container runs as uid 100 (odoo) and must be able to read its config
sudo chown 100:101 odoo.prod.conf

# Create the database with the add-on and demo data, then start everything
docker compose -f compose.prod.yaml run --rm odoo odoo -c /etc/odoo/odoo.conf \
  -d rioclaro -i rioclaro_invoice_inbox --stop-after-init
docker compose -f compose.prod.yaml up -d
```

Set the Odoo `admin` password from the UI (Preferences) on first login, then add
the `RIOCLARO_*` keys to `.env` and run `docker compose -f compose.prod.yaml up -d`
again. Do not set `RIOCLARO_TELEGRAM_BOT_TOKEN` here while another instance polls
the same bot.

## Update

```bash
cd ~/app && git pull && cd deploy
docker compose -f compose.prod.yaml pull
docker compose -f compose.prod.yaml run --rm odoo odoo -c /etc/odoo/odoo.conf \
  -d rioclaro -u rioclaro_invoice_inbox --stop-after-init
docker compose -f compose.prod.yaml up -d
```

## Checks

- `curl -sI https://rioclaro.ropech.com/web/login` returns `200`.
- `curl -m 5 http://20.151.139.73:8069` fails (port not published).
- `https://rioclaro.ropech.com/web/database/manager` returns 404 (blocked in Caddy and `list_db = False`).
