# Kubernetes manifests

A functional core set that mirrors `docker-compose.prod.yml` — enough to deploy Vicero on a
cluster. **Not** a full Helm chart (templating, HPA autoscaling, PodDisruptionBudgets,
NetworkPolicies, and managed-DB wiring are the documented stretch — see PROGRESS roadmap).

## Files
- `namespace.yaml` — the `vicero` namespace.
- `config.yaml` — ConfigMap (non-secret env) + a Secret **template** (replace placeholders!).
- `datastores.yaml` — Postgres (StatefulSet + PVC) + Redis. Prefer managed services in real prod.
- `app.yaml` — one-shot migrate Job, plus api / worker / beat / web Deployments + Services, with
  liveness/readiness probes and resource requests.
- `ingress.yaml` — TLS ingress (cert-manager) for `$DOMAIN` (web) and `$API_DOMAIN` (api).

## Deploy
```bash
# 0. Build + push images (CI does this — see .github/workflows/ci.yml), then set image refs
#    (ghcr.io/OWNER/vicero-{api,web}) in app.yaml.
# 1. Fill in Secret values in config.yaml (or use an external-secrets operator).
kubectl apply -f namespace.yaml
kubectl apply -f config.yaml -f datastores.yaml
kubectl apply -f app.yaml       # runs the migrate Job, then api/worker/beat/web
kubectl apply -f ingress.yaml   # needs an ingress controller + cert-manager ClusterIssuer
kubectl -n vicero rollout status deploy/api
```

## Notes
- **Migrations** run as a one-shot `Job` (`vicero-migrate`); api/worker don't migrate on start.
  In CD, delete + re-apply the Job (or template a unique name) each deploy.
- **Realtime** fan-out uses Redis pub/sub (ADR-028), so scaling `api`/`web` replicas is safe.
- **beat** is pinned to a single replica (only one scheduler should run).
- Point `DATABASE_URL`/`REDIS_URL` at managed services and drop `datastores.yaml` for production.
