# Ingress-NGINX General Handbook

This handbook provides general knowledge about ingress-nginx. It is not tied to
any specific benchmark case.

## Core components

- Namespace: typically `ingress-nginx`.
- Controller Deployment: `ingress-nginx-controller`.
- Controller Service: `ingress-nginx-controller` (ClusterIP, sometimes NodePort).
- ConfigMap: `ingress-nginx-controller` (controller configuration).

Common inspect commands:
```bash
kubectl -n ingress-nginx get deploy,svc,cm
kubectl -n ingress-nginx logs deploy/ingress-nginx-controller --tail=200
```

## Ingress class selection

Ingress routing is scoped by class.

- Ingress resource uses `spec.ingressClassName`.
- Controller uses flags such as:
  - `--ingress-class=<name>`
  - `--controller-class=<id>`
  - `--watch-ingress-without-class=false`
- IngressClass objects define valid class names.

When `--watch-ingress-without-class=false` is set, Ingresses without a class are
ignored by the controller.

## ConfigMap usage

The controller reads configuration from its ConfigMap. Many behaviors are
controlled there, including logging, observability, and feature toggles.
Changes usually require a reload (sometimes a rollout restart).

Common operations:
```bash
kubectl -n ingress-nginx get cm ingress-nginx-controller -o yaml
kubectl -n ingress-nginx edit cm ingress-nginx-controller
kubectl -n ingress-nginx rollout restart deploy/ingress-nginx-controller
```

## Common annotations (examples)

Canary routing:
- `nginx.ingress.kubernetes.io/canary: "true"`
- `nginx.ingress.kubernetes.io/canary-by-header: "Header-Name"`
- `nginx.ingress.kubernetes.io/canary-by-header-value: "value"`
- `nginx.ingress.kubernetes.io/canary-weight: "<0-100>"`

Rate limiting:
- `nginx.ingress.kubernetes.io/limit-rps: "<rps>"`
- `nginx.ingress.kubernetes.io/limit-req-status-code: "429"`
- `nginx.ingress.kubernetes.io/limit-burst: "<burst>"`

Path rewriting:
- `nginx.ingress.kubernetes.io/rewrite-target: "/"`
- `nginx.ingress.kubernetes.io/use-regex: "true"`

TLS and protocol:
- `nginx.ingress.kubernetes.io/ssl-redirect: "true"`
- `nginx.ingress.kubernetes.io/backend-protocol: "HTTP"` or `"HTTPS"`

## TLS basics

- TLS secrets use type `kubernetes.io/tls` with keys `tls.crt` and `tls.key`.
- The Ingress `tls` block references the secret by name.
- For client-side validation, ensure the client trusts the issuing CA.

## Observability

- Access logs are emitted by the controller pods.
- Log format can be set via ConfigMap (for example `log-format-upstream`).
- OpenTelemetry support is configured via ConfigMap flags and collector address.

## Behavior with replicas

- Some features (rate limits, canary weight) may be enforced per-controller pod.
- With multiple replicas, traffic distribution can affect deterministic behavior.

## Troubleshooting checklist

- Ingress not routing: check class name, controller flags, and ingress events.
- 404 or wrong backend: check path rules and service name/port.
- TLS errors: verify secret, certificate validity, and SNI host.
- Canary not working: confirm canary annotations and header/weight conditions.
