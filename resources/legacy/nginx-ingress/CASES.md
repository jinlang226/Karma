# Ingress-NGINX Runbook (Benchmark)

This runbook covers all ingress-nginx benchmark cases in `resources/nginx-ingress/`.
For general Kubernetes knowledge, see `resources/KUBERNETES.md`. For ingress-nginx
background, see `resources/nginx-ingress/GENERAL.md`.

## Common environment

- Controller: ingress-nginx installed from the official kind manifest.
- Primary namespace: `ingress-nginx`.
- Test namespace: `demo`.
- Test pod: `curl-test` in `demo` (use it for all requests).
- Ingress controller service (inside cluster):
  - `ingress-nginx-controller.ingress-nginx.svc`.
- Default ingress class from the manifest is `nginx`.
- Some cases explicitly set `--watch-ingress-without-class=false` on a controller,
  which means you must set `spec.ingressClassName` on the Ingress.

Common debug commands:
```bash
kubectl get ingress -A
kubectl get ingressclass
kubectl -n ingress-nginx logs deploy/ingress-nginx-controller --tail=200
kubectl -n demo get svc,deploy,pod
```

## Case index

- `renew_tls_secret`: expired TLS certificate rotation
- `otel_log_format`: OpenTelemetry trace/span IDs in access logs
- `create_ingress`: create Service + Ingress for an existing app
- `rate_limit_ingress_easy`: rate limiting with single controller replica
- `rate_limit_replica_hard`: rate limiting with 3 controller replicas
- `class_only_upgrade`: ingress class selection with public/internal controllers
- `ingress_canary`: header-based canary with /health pinned to stable

---

## Case: renew_tls_secret

Folder: `resources/nginx-ingress/renew_tls_secret`

Scenario
- An Ingress for `demo.example.com` exists with TLS enabled.
- The leaf certificate in the secret is expired.
- The app backend is healthy and serves `hello`.

Key resources
- Namespace: `demo`.
- Ingress: `demo-ingress`.
- TLS secret: `expired-tls-secret`.
- CA ConfigMap: `test-ca` (mounted in curl-test at `/tmp/tls/ca.crt`).
- Test helper script: `/tmp/test_ingress.sh` (copied into curl-test).
- Host-side env file: `/tmp/ingress_env` (contains `INGRESS_NODE_IP`, `INGRESS_HTTPS_PORT`).
- Certificate config files:
  - `resource/leaf.cnf`
  - `resource/ca-ext.conf`

Validation
```bash
source /tmp/ingress_env
kubectl -n demo exec curl-test -- /tmp/test_ingress.sh \
  $INGRESS_HTTPS_PORT $INGRESS_NODE_IP demo.example.com
```
Or:
```bash
source /tmp/ingress_env
kubectl -n demo exec curl-test -- sh -c \
'curl -v --cacert /tmp/tls/ca.crt \
  --resolve demo.example.com:'"$INGRESS_HTTPS_PORT"':'"$INGRESS_NODE_IP"' \
  https://demo.example.com:'"$INGRESS_HTTPS_PORT"'/'
```

Success criteria
- HTTPS request exits 0 and prints `hello`.

---

## Case: otel_log_format

Folder: `resources/nginx-ingress/otel_log_format`

Scenario
- Ingress `otel.example.com` routes to a healthy backend.
- Requests succeed, but access logs lack OpenTelemetry trace/span IDs.
- OpenTelemetry Collector is already running.

Key resources
- Namespace: `demo` (app + ingress).
- Namespace: `otel` (collector).
- Collector service: `otel-collector.otel.svc:4317` (OTLP/gRPC).
- Ingress: `otel-echo`.

Validation
```bash
kubectl -n demo exec curl-test -- curl -sS -H "Host: otel.example.com" \
  http://ingress-nginx-controller.ingress-nginx.svc/otel-check
```
Then check logs:
```bash
kubectl -n ingress-nginx logs deploy/ingress-nginx-controller --tail=200
kubectl -n otel logs deploy/otel-collector --tail=200
```

Success criteria
- The controller access log line for `/otel-check` includes non-empty
  `otel_trace_id` and `otel_span_id` values.
- The corresponding trace is exported to the collector (visible in collector logs).
---

## Case: create_ingress

Folder: `resources/nginx-ingress/create_ingress`

Scenario
- The demo app is running in `demo` on port 5678.
- It only serves the root path `/`.
- There is no Service or Ingress yet.
- The controller is configured with `--watch-ingress-without-class=false`.

Key resources
- Deployment: `demo-app` in `demo`.
- No Service exists until created by the agent.

Requirements
- Expose the app at `http://demo.example.com/app`.
- Do not modify the application Deployment.
- Use ingress class `nginx`.

Validation
```bash
kubectl -n demo exec curl-test -- curl -sS -H "Host: demo.example.com" \
  http://ingress-nginx-controller.ingress-nginx.svc/app
```

Success criteria
- Command exits 0 and prints `hello`.

---

## Case: rate_limit_ingress_easy

Folder: `resources/nginx-ingress/rate_limit_ingress_easy`

Scenario
- Two Ingresses exist for `rate.example.com`:
  - `/api` (should be rate limited)
  - `/health` (must remain unlimited)
- The backend is healthy.
- Ingress controller replicas: 1.

Key resources
- Deployment/Service: `rate-echo` in `demo`.
- Ingresses: `rate-api`, `rate-health`.
- Ingress class: `nginx`.

Requirements
- Limit `/api` to 1 QPS.
- Rate-limited responses must return HTTP 429.
- Do not modify the application Deployment.

Validation
```bash
kubectl -n demo exec curl-test -- sh -c \
'for i in 1 2 3 4 5 6 7 8 9 10; do curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Host: rate.example.com" http://ingress-nginx-controller.ingress-nginx.svc/api; \
  sleep 0.5; done'
```
```bash
kubectl -n demo exec curl-test -- sh -c \
'for i in 1 2 3 4 5 6 7 8 9 10; do curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Host: rate.example.com" http://ingress-nginx-controller.ingress-nginx.svc/health; \
  sleep 0.5; done'
```

Success criteria
- `/api`: at least 4 of 10 responses are 429.
- `/health`: all responses are 200.

---

## Case: rate_limit_replica_hard

Folder: `resources/nginx-ingress/rate_limit_replica_hard`

Scenario
- Same as `rate_limit_ingress_easy`, but controller replicas: 3.

Validation and success criteria
- Same as `rate_limit_ingress_easy`.

---

## Case: class_only_upgrade

Folder: `resources/nginx-ingress/class_only_upgrade`

Scenario
- Two ingress-nginx controllers are running (ingress-1 and ingress-2).
- Both controllers ignore Ingresses without a class.
- The demo Ingress has no class and is not being picked up.

Key resources
- Demo ingress: `demo-app` in `demo`.
- Gateway service for testing: `ingress-gateway.demo.svc.cluster.local`.

Requirements
- Update the demo Ingress so requests to `class.example.com` succeed.
- Do not modify the application Deployment or Service.

Validation
```bash
kubectl -n demo exec curl-test -- curl -sS -H "Host: class.example.com" \
  http://ingress-gateway.demo.svc.cluster.local/
```

Success criteria
- Command exits 0 and prints `hello`.

---

## Case: ingress_canary

Folder: `resources/nginx-ingress/ingress_canary`

Scenario
- Two backends exist:
  - `stable-echo` returns `stable`.
  - `canary-echo` returns `canary`.
- A stable Ingress routes `canary.example.com` to the stable backend.
- A canary Ingress exists, but canary traffic never reaches the canary backend.
- The `/health` path must always be handled by the stable backend.

Key resources
- Services: `stable-echo`, `canary-echo` in `demo`.
- Ingresses: `canary-stable`, `canary-canary`.
- Host: `canary.example.com`.

Requirements
- Requests with header `X-Canary: always` must route to the canary backend.
- Requests without that header must route to the stable backend.
- `/health` must always route to the stable backend, even with the header.
- Do not modify the application Deployments or Services.

Validation
```bash
kubectl -n demo exec curl-test -- curl -sS -H "Host: canary.example.com" \
  http://ingress-nginx-controller.ingress-nginx.svc/
```
```bash
kubectl -n demo exec curl-test -- curl -sS -H "Host: canary.example.com" \
  -H "X-Canary: always" \
  http://ingress-nginx-controller.ingress-nginx.svc/
```
```bash
kubectl -n demo exec curl-test -- curl -sS -H "Host: canary.example.com" \
  http://ingress-nginx-controller.ingress-nginx.svc/health
```
```bash
kubectl -n demo exec curl-test -- curl -sS -H "Host: canary.example.com" \
  -H "X-Canary: always" \
  http://ingress-nginx-controller.ingress-nginx.svc/health
```

Success criteria
- Without header: body is `stable`.
- With header: body is `canary` for `/` and `stable` for `/health`.
