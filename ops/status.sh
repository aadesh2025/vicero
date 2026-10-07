#!/usr/bin/env bash
# One-screen health report. Read-only; prints no secrets. Usage: /opt/vicero/status.sh
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/lib.sh"
DOMAIN="$(env_get DOMAIN)"; API_DOMAIN="$(env_get API_DOMAIN)"

echo "=== containers"
docker ps -a --format '{{.Names}} | {{.Status}}' | sort
echo; echo "=== memory (containers)"
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' | sort
free -h | sed -n 1,3p
# Low-memory alert (n8n at 768m + Ollama can leave little headroom on this 3.7 GB host). Thresholds: MemAvailable.
avail_mb="$(awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo)"
swap_used_mb="$(awk '/SwapTotal/ {t=$2} /SwapFree/ {f=$2} END {printf "%d", (t-f)/1024}' /proc/meminfo)"
if [ "$avail_mb" -lt 300 ]; then
  echo "ALERT CRITICAL: only ${avail_mb} MB memory available (swap used ${swap_used_mb} MB). Act now:"
  echo "   1) lower worker concurrency   2) lower N8N_MEM_LIMIT   3) ask before touching anything else (docs/25 §11)"
elif [ "$avail_mb" -lt 600 ]; then
  echo "ALERT WARNING: ${avail_mb} MB memory available (swap used ${swap_used_mb} MB). Watch it; steps in docs/25 §11."
else
  echo "memory OK: ${avail_mb} MB available (swap used ${swap_used_mb} MB)"
fi
oom="$(for c in $(docker ps -aq 2>/dev/null); do [ "$(docker inspect -f '{{.State.OOMKilled}}' "$c" 2>/dev/null)" = true ] && docker inspect -f '{{.Name}}' "$c"; done)"
[ -n "$oom" ] && echo "ALERT: OOM-killed container(s): $oom"
echo; echo "=== disk"
df -h / | tail -1
docker system df --format '{{.Type}}: {{.Size}} (reclaimable {{.Reclaimable}})' 2>/dev/null
echo; echo "=== backups"
echo -n "local newest dump: "; docker exec vicero-prod-backup-1 sh -c 'ls -lt /backups/vicero_vicero_*.sql.gz 2>/dev/null | head -1 | awk "{print \$6, \$7, \$8, \$9}"' 2>/dev/null || echo unknown
echo -n "off-server (Drive) last run: "; grep -E '== .* (ok|start)$' /var/log/vicero-offsite.log 2>/dev/null | tail -1 || echo "no log"
echo -n "newest on Drive: "; rclone lsf gdrivecrypt: --include 'vicero_vicero_*.sql.gz' 2>/dev/null | sort | tail -1
echo; echo "=== TLS certificates"
for d in "$DOMAIN" "$API_DOMAIN"; do
  echo -n "$d expires: "; echo | openssl s_client -connect "$d:443" -servername "$d" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2
done
echo; echo "=== HTTPS health"
echo -n "https://$API_DOMAIN/readyz  -> "; curl -sS -m 15 "https://$API_DOMAIN/readyz" || echo FAIL; echo
echo -n "https://$DOMAIN/login -> HTTP "; curl -sS -m 15 -o /dev/null -w '%{http_code}\n' "https://$DOMAIN/login" || echo FAIL
N8N_DOMAIN_="$(env_get N8N_DOMAIN)"
if [ -n "$N8N_DOMAIN_" ]; then
  echo -n "n8n editor (expect 401 without the gate password): https://$N8N_DOMAIN_/ -> HTTP "; curl -sS -m 15 -o /dev/null -w '%{http_code}\n' "https://$N8N_DOMAIN_/" || echo FAIL
  echo -n "n8n public /webhook/x (expect 404): HTTP "; curl -sS -m 15 -o /dev/null -w '%{http_code}\n' "https://$N8N_DOMAIN_/webhook/x" || echo FAIL
  echo -n "n8n container: "; docker inspect -f '{{.State.Health.Status}}' vicero-prod-n8n-1 2>/dev/null || echo "not running"
fi
echo; echo "=== running images"
docker ps --format '{{.Names}} {{.Image}}' | grep -E 'api|web' | sort
echo; echo "=== repo"
git -C "$ROOT" log -1 --format='%h %s (%cr)'
git -C "$ROOT" status --short | head -5
echo -n "uptime: "; uptime -p
