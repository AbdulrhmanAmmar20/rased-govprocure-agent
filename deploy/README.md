# Deployment

## Order of operations

```bash
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-configmap.yaml
# 02-secret.example.yaml is a TEMPLATE - do not apply it. See below.
kubectl apply -f k8s/03-service.yaml
kubectl apply -f k8s/04-deployment.yaml
kubectl apply -f k8s/05-networkpolicy.yaml
kubectl apply -f k8s/06-autoscaling.yaml
```

Apply the NetworkPolicies **before** exposing the service. Applying them after
leaves a window in which the API can reach the internet, which is precisely
the condition NFR-1.1 exists to exclude.

## Secrets

Do not apply `02-secret.example.yaml`. It is a template, and a `Secret`
applied from a manifest sits base64-encoded — not encrypted — in etcd.

Bind these through the entity's managed secret store using external-secrets or
a CSI driver:

| Key | Value |
|---|---|
| `RASED_JWT_SECRET` | `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `RASED_VAULT_KEY` | **Leave empty.** |

An empty vault key is the correct production setting, not an omission. Each
pod then mints its own ephemeral AES-256 key, so a restart cryptographically
destroys every outstanding masking table and there is no long-lived key to
steal, escrow, or rotate.

## Building the image

```bash
docker build -t registry.internal.gov.sa/rased/govprocure-agent:1.0.0 .
```

The build resolves Python dependencies from whatever index pip is configured
to use. In the sovereign pipeline that must be the in-Kingdom mirror. A build
host that reaches a public index is a supply-chain path out of the Kingdom
even though the running service has none — configure `PIP_INDEX_URL` on the
builder and verify it, rather than assuming.

## Pre-flight checklist

Before the first production deployment:

- [ ] Regulation corpus replaced with the gazetted text and
      `source_status` set to `official` (`docs/corpus-governance.md`)
- [ ] A Compliance Auditor has signed off on the corpus diff
- [ ] `RASED_ENVIRONMENT=production` — this is what withholds the development
      token endpoint and the OpenAPI document
- [ ] `RASED_JWT_SECRET` bound from the managed secret store
- [ ] `RASED_VAULT_KEY` left empty
- [ ] Storage class for the audit PVC provides AES-256 at rest
- [ ] Ingress terminates TLS 1.3
- [ ] Rate limiting configured at the ingress (not implemented in the app)
- [ ] Transaction store moved out of process memory, or replicas pinned to 1
- [ ] Audit trail backup and restore procedure defined and tested
- [ ] `kubectl exec` into a pod and confirm `curl https://api.openai.com`
      fails — the NetworkPolicy is only real once it has been observed to deny

The last item is worth doing by hand every time. A NetworkPolicy that was
never tested is indistinguishable from one the CNI silently ignored.

## Verifying a running deployment

```bash
kubectl -n rased port-forward svc/rased-api 8000:80

curl -s localhost:8000/api/v1/health/ready | python -m json.tool
```

Check `corpus_status` in the response. If it is anything other than
`official`, reports carry a disclaimer and are not legally certifiable.

## Replica count and the transaction store

Until the transaction store moves to Redis or Postgres, a suspended
transaction lives in one pod's memory. With three replicas, a Director's
approval has roughly a one-in-three chance of reaching the pod that holds it,
and otherwise returns 404.

Two valid interim options:

1. **Pin `replicas: 1`** and accept the availability cost, or
2. **Session affinity** on the ingress keyed to the transaction id.

Neither is a substitute for the fix. See `docs/architecture.md`.

## Audit trail operations

Each replica writes its own chain to its own PVC. To verify one:

```bash
kubectl -n rased exec deploy/rased-api -- python -m app.cli verify-audit
```

To verify every chain, run it against each pod individually — a passing check
on one pod says nothing about the others.
