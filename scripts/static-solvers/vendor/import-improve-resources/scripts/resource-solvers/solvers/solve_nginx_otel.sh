#!/bin/sh
set -eu

ingress_ns="$BENCH_NS_INGRESS"
app_ns="$BENCH_NS_APP"
otel_ns="$BENCH_NS_OTEL"
ingress="${BENCH_PARAM_INGRESS_NAME:-otel-echo}"
collector="${BENCH_PARAM_COLLECTOR_SERVICE_NAME:-otel-collector}"
port="${BENCH_PARAM_COLLECTOR_PORT:-4317}"
format="${BENCH_PARAM_OTEL_LOG_FORMAT:-\$remote_addr - \$remote_user [\$time_local] \"\$request\" \$status \$body_bytes_sent \"\$http_referer\" \"\$http_user_agent\" otel_trace_id=\$opentelemetry_trace_id otel_span_id=\$opentelemetry_span_id}"
host="${collector}.${otel_ns}.svc.cluster.local"

patch=$(printf '{"data":{"enable-opentelemetry":"true","otlp-collector-host":"%s","otlp-collector-port":"%s","otel-sampler":"AlwaysOn","otel-sampler-ratio":"1.0","log-format-upstream":"%s"}}' \
  "$host" "$port" "$(printf '%s' "$format" | sed 's/\\/\\\\/g; s/"/\\"/g')")
kubectl -n "$ingress_ns" patch configmap ingress-nginx-controller --type=merge -p "$patch"
kubectl -n "$app_ns" annotate ingress "$ingress" \
  nginx.ingress.kubernetes.io/enable-opentelemetry=true --overwrite
kubectl -n "$ingress_ns" rollout restart deployment/ingress-nginx-controller
kubectl -n "$ingress_ns" rollout status deployment/ingress-nginx-controller --timeout=180s
printf 'enabled ingress OpenTelemetry\n' > submit.txt
