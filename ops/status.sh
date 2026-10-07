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
echo; echo "=== running images"
docker ps --format '{{.Names}} {{.Image}}' | grep -E 'api|web' | sort
echo; echo "=== repo"
git -C "$ROOT" log -1 --format='%h %s (%cr)'
git -C "$ROOT" status --short | head -5
echo -n "uptime: "; uptime -p
