#!/usr/bin/env bash
# 瑞斯图尔法庭 · 一键部署（Ubuntu 24.04）
# 在服务器上执行：
#   curl -fsSL https://raw.githubusercontent.com/fishrz/court-of-ristul/master/deploy/bootstrap.sh | sudo bash
# 幂等：可重复执行以更新部署。
set -euo pipefail

REPO="https://github.com/fishrz/court-of-ristul.git"
APP_DIR="/opt/court-of-ristul"
APP_USER="ubuntu"
DOMAIN="ristul.icu"

log() { echo -e "\n\033[1;33m==> $*\033[0m"; }

log "1/7 系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl python3-venv python3-pip sqlite3 \
	debian-keyring debian-archive-keyring apt-transport-https

log "2/7 安装 Caddy"
if ! command -v caddy >/dev/null; then
	curl -fsSL 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
		| gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	curl -fsSL 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
		> /etc/apt/sources.list.d/caddy-stable.list
	apt-get update -qq
	apt-get install -y -qq caddy
fi
caddy version

log "3/7 拉取代码"
if [ -d "$APP_DIR/.git" ]; then
	git -C "$APP_DIR" fetch --all -q
	git -C "$APP_DIR" reset --hard origin/master -q
else
	rm -rf "$APP_DIR"
	git clone -q "$REPO" "$APP_DIR"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

log "4/7 Python 虚拟环境"
cd "$APP_DIR/backend"
sudo -u "$APP_USER" python3 -m venv .venv 2>/dev/null || true
sudo -u "$APP_USER" .venv/bin/pip install -q --upgrade pip
sudo -u "$APP_USER" .venv/bin/pip install -q -r requirements.txt

log "4.5/7 组装前端发布目录 web/"
# 仓库根混放着前端资源、backend/、corpus/、.git/，不能整个当 web root。
# 只挑真正要给浏览器的文件。
rm -rf "$APP_DIR/web"
mkdir -p "$APP_DIR/web"
for f in index.html data-layer.js live-layer.js trial-bridge.js \
	dossier.js memes-engine.js memes.json; do
	cp "$APP_DIR/$f" "$APP_DIR/web/"
done
chown -R "$APP_USER:$APP_USER" "$APP_DIR/web"
ls -la "$APP_DIR/web"

log "5/7 清理占位玩家（队友走 /join 自助登记真实 Steam 账号）"
# 不再自动 seed：占位玩家 steam_id 是编的，轮询器抓不到他们的比赛，
# 只会让投票和 AI 判决在一堆抓不到数据的假人之间打转。
if [ -f "$APP_DIR/backend/court.db" ]; then
	sudo -u "$APP_USER" .venv/bin/python -m scripts.purge_seed_players --apply || \
		echo "  清理脚本失败，跳过"
fi

log "6/7 systemd 后端服务"
cp "$APP_DIR/deploy/court-backend.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q court-backend
systemctl restart court-backend
sleep 3
systemctl is-active court-backend || { journalctl -u court-backend -n 30 --no-pager; exit 1; }

log "7/7 Caddy 反向代理 + 自动 HTTPS"
cp "$APP_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy
sleep 3

log "自检"
echo -n "  backend /health : "; curl -s -m 5 http://127.0.0.1:8010/health || echo FAIL
echo -n "  local  /api     : "; curl -s -o /dev/null -w '%{http_code}\n' -m 5 http://127.0.0.1:8010/api/matches
echo -n "  https  首页     : "; curl -s -o /dev/null -w '%{http_code}\n' -m 20 "https://$DOMAIN/" || echo "证书可能仍在签发，稍等 30s 重试"

echo -e "\n\033[1;32m部署完成 → https://$DOMAIN\033[0m"
