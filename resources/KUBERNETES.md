# Kubernetes Runbook (General)

This runbook is shared across all benchmark suites. It covers common Kubernetes
concepts and troubleshooting workflows used by the tasks.

## Core resources

- Namespace: logical isolation for resources.
- Pod: the basic workload unit.
- Deployment: manages replicated Pods and rollouts.
- Service: stable virtual IP and load balancing for Pods.
- Ingress: HTTP(S) routing to Services via an ingress controller.
- ConfigMap / Secret: configuration and sensitive data.
- NetworkPolicy: namespace or pod-level traffic rules.

## Common commands

Inspect resources:
```bash
kubectl get ns
kubectl get pod -A
kubectl get deploy -A
kubectl get svc -A
kubectl get ingress -A
kubectl get cm -A
kubectl get secret -A
```

Describe and debug:
```bash
kubectl describe pod <pod> -n <ns>
kubectl describe deploy <deploy> -n <ns>
kubectl describe svc <svc> -n <ns>
kubectl describe ingress <ing> -n <ns>
```

Logs and events:
```bash
kubectl logs <pod> -n <ns>
kubectl logs deploy/<deploy> -n <ns>
kubectl get events -n <ns> --sort-by=.lastTimestamp
```

Apply and edit:
```bash
kubectl apply -f <file.yaml>
kubectl edit <kind>/<name> -n <ns>
```

Rollouts:
```bash
kubectl rollout status deploy/<deploy> -n <ns>
kubectl rollout restart deploy/<deploy> -n <ns>
```

Exec into a pod:
```bash
kubectl exec -n <ns> -it <pod> -- sh
```

## Networking basics

- Service DNS name: `<svc>.<ns>.svc`.
- ClusterIP is reachable only inside the cluster.
- Ingress requires an ingress controller (not just an Ingress resource).
- Endpoints for a Service should match the Deployment labels.

Check Service endpoints:
```bash
kubectl get endpoints <svc> -n <ns>
```

## Debug flow (typical)

1. Verify namespace and resource names.
2. Check Pod readiness and restarts.
3. Check Service selectors and endpoints.
4. Check Ingress rules and controller logs.
5. Check events for warnings or errors.
6. If traffic is blocked, check NetworkPolicy.

## NetworkPolicy tips

- Policies are namespace-scoped.
- If any Ingress policy selects a Pod, traffic is denied by default unless
  explicitly allowed.
- Validate label selectors carefully.

## Safety guidelines

- Prefer targeted edits (single resource) over namespace deletes.
- Avoid broad selectors in delete commands.
- Confirm resource names and namespaces before applying changes.
