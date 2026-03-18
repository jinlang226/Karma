# Ingress-NGINX Runbook (Base Cases)

This runbook covers the active base-functionality benchmark cases in
`resources/nginx-ingress/`.

Historical scenario-shaped folders such as `create_ingress/` and
`renew_tls_secret/` are still present because the new base cases reuse their
resource manifests, but those legacy folders no longer expose active
`test.yaml` entrypoints.

For general Kubernetes knowledge, see `resources/KUBERNETES.md`. For
ingress-nginx background, see `resources/nginx-ingress/GENERAL.md`.

## Common environment

- Controller namespace role: `${BENCH_NS_INGRESS}`
- Application namespace role: `${BENCH_NS_APP}`
- Optional collector namespace role: `${BENCH_NS_OTEL}`
- Controller Service inside the cluster:
  - `ingress-nginx-controller.${BENCH_NS_INGRESS}.svc.cluster.local`
- The base cases prefer explicit `spec.ingressClassName: nginx`.

Helpful commands:
```bash
kubectl -n ${BENCH_NS_INGRESS} get deploy,svc,pod
kubectl -n ${BENCH_NS_APP} get deploy,svc,ingress,pod
kubectl -n ${BENCH_NS_INGRESS} logs deploy/ingress-nginx-controller --tail=200
```

## Active case index

- `ingress_route_ready`: create a basic Service + Ingress route for an existing app
- `ingress_class_routing`: bind an Ingress to the required class when the controller ignores classless Ingresses
- `header_canary_routing`: route stable traffic by default and canary traffic when a header is present
- `rate_limit_ingress`: apply per-Ingress rate limiting while keeping a health path unlimited
- `https_ingress_ready`: create a valid TLS Secret + HTTPS Ingress for an existing backend
- `otel_ingress_logging_ready`: enable ingress-scoped OpenTelemetry logging and export

## Case: ingress_route_ready

Folder: `resources/nginx-ingress/ingress_route_ready`

Capability:
- Create a working Service + Ingress route for an existing Deployment.

Baseline:
- The app Deployment exists and is healthy.
- No Service exists yet.
- No Ingress exists yet.

Success:
- Requests to the configured host/path return `hello`.

## Case: ingress_class_routing

Folder: `resources/nginx-ingress/ingress_class_routing`

Capability:
- Make an Ingress route correctly when the controller requires an explicit class.

Baseline:
- The backend Deployment and Service already exist.
- The controller is configured with `--watch-ingress-without-class=false`.
- The Ingress exists without `spec.ingressClassName`.

Success:
- The Ingress sets `spec.ingressClassName=nginx` and traffic succeeds.

## Case: header_canary_routing

Folder: `resources/nginx-ingress/header_canary_routing`

Capability:
- Route stable traffic by default and canary traffic when a header is present.

Baseline:
- Stable and canary backends already exist.
- Stable ingress is healthy.
- Canary ingress exists, but its header value is wrong.

Success:
- Requests without the header return `stable`.
- Requests with the header return `canary`.

## Case: rate_limit_ingress

Folder: `resources/nginx-ingress/rate_limit_ingress`

Capability:
- Apply per-Ingress rate limiting to an API path.

Baseline:
- The backend already exists.
- API and health Ingresses already exist.
- The controller runs with one replica.
- No rate-limit annotations are configured yet.
- The controller ConfigMap still uses the baseline throttled response code.

Success:
- The controller ConfigMap sets `limit-req-status-code` to `429`.
- The API path returns repeated `429` responses under load.
- The health path continues returning only `200`.

## Case: https_ingress_ready

Folder: `resources/nginx-ingress/https_ingress_ready`

Capability:
- Terminate HTTPS with a valid TLS Secret for an existing backend.

Baseline:
- The backend Deployment and Service already exist.
- No TLS Secret exists yet.
- No HTTPS Ingress exists yet.

Success:
- The TLS Secret contains a valid certificate for the host.
- HTTPS requests return `hello`.

## Case: otel_ingress_logging_ready

Folder: `resources/nginx-ingress/otel_ingress_logging_ready`

Capability:
- Enable ingress-scoped OpenTelemetry logging and export.

Baseline:
- The app Ingress already routes traffic.
- The collector already exists.
- OpenTelemetry is disabled in the controller ConfigMap and on the Ingress.

Success:
- Controller access logs include non-empty trace and span IDs.
- The collector receives the exported trace.
