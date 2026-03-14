# Apache Spark on Kubernetes General Handbook

This handbook provides general knowledge about running Apache Spark on Kubernetes.
It is not tied to any specific benchmark case.

## Core Components

- **Spark Operator**: Manages SparkApplication custom resources
  - Namespace: typically `spark-operator`
  - Deployment: `spark-operator`
  - ServiceAccount: `spark-operator`

- **Spark Applications**: Run as custom resources (SparkApplication CRD)
  - Driver Pod: The main application controller
  - Executor Pods: Worker pods that execute tasks
  - Namespace: typically `default` or `spark-apps`

Common inspect commands:
```bash
# Check Spark Operator status
kubectl -n spark-operator get deploy,svc,pod

# List all SparkApplications
kubectl get sparkapplications -A

# Check driver and executor pods
kubectl get pods -l spark-role=driver
kubectl get pods -l spark-role=executor

# View SparkApplication status
kubectl describe sparkapplication <app-name>
```

## Spark Operator Installation

The Spark Operator is typically installed via Helm:
```bash
helm repo add spark-operator https://googlecloudplatform.github.io/spark-on-k8s-operator
helm install spark-operator spark-operator/spark-operator --namespace spark-operator --create-namespace
```

Or using kubectl with the manifest:
```bash
kubectl apply -f https://github.com/GoogleCloudPlatform/spark-on-k8s-operator/releases/download/v1beta2-1.4.0-3.5.0/spark-operator.yaml
```

## SparkApplication CRD

A SparkApplication defines:
- Application type (Scala, Python, Java, R)
- Driver and executor resource requirements
- Docker image to use
- Application arguments
- Dependencies (jars, files, etc.)

Basic structure:
```yaml
apiVersion: sparkoperator.k8s.io/v1beta2
kind: SparkApplication
metadata:
  name: spark-pi
  namespace: default
spec:
  type: Scala
  mode: cluster
  image: spark:3.5.0
  mainClass: org.apache.spark.examples.SparkPi
  mainApplicationFile: local:///opt/spark/examples/jars/spark-examples.jar
  sparkVersion: 3.5.0
  driver:
    cores: 1
    memory: 512m
    serviceAccount: spark
  executor:
    cores: 1
    instances: 2
    memory: 512m
```

## SparkApplication Lifecycle

1. **Submitted**: User creates the SparkApplication resource
2. **Running**: Operator creates driver pod, driver creates executor pods
3. **Completed**: All tasks finished successfully
4. **Failed**: Application encountered errors
5. **Unknown**: Status cannot be determined

View lifecycle:
```bash
kubectl get sparkapplication <app-name> -o jsonpath='{.status.applicationState.state}'
```

## Common Operations

### View logs
```bash
# Driver logs
kubectl logs <driver-pod-name>

# Executor logs
kubectl logs <executor-pod-name>

# Follow logs in real-time
kubectl logs -f <pod-name>
```

### Scale executors
You can modify the number of executors:
```bash
kubectl patch sparkapplication <app-name> --type=merge -p '{"spec":{"executor":{"instances":5}}}'
```

Or edit directly:
```bash
kubectl edit sparkapplication <app-name>
```

### Delete SparkApplication
```bash
kubectl delete sparkapplication <app-name>
```

The operator will automatically clean up driver and executor pods.

## Resource Management

- **Driver**: Usually requires modest resources (1-2 cores, 512MB-1GB memory)
- **Executors**: Scale based on workload (1-4 cores each, 1GB-4GB memory)
- **Total resources**: `(driver resources) + (executor resources × num executors)`

## ServiceAccounts and RBAC

SparkApplications require proper RBAC permissions:
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: spark
  namespace: default
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: spark-role
rules:
- apiGroups: [""]
  resources: ["pods", "services", "configmaps"]
  verbs: ["create", "get", "list", "watch", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: spark-role-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: spark-role
subjects:
- kind: ServiceAccount
  name: spark
  namespace: default
```

## Monitoring and Debugging

### Check application status
```bash
kubectl get sparkapplication <app-name> -o yaml
```

### View events
```bash
kubectl get events --sort-by='.lastTimestamp' | grep spark
```

### Common issues

1. **Pods stuck in Pending**: Check resource quotas and node capacity
2. **ImagePullBackOff**: Verify image name and pull secrets
3. **CrashLoopBackOff**: Check logs for application errors
4. **Service account errors**: Verify RBAC permissions

### Spark UI Access

The Spark UI can be accessed via port-forwarding:
```bash
kubectl port-forward <driver-pod-name> 4040:4040
# Then open http://localhost:4040 in browser
```

## Example Applications

Spark includes several example applications:
- **SparkPi**: Calculates Pi using Monte Carlo method
- **WordCount**: Classic MapReduce word counting
- **GroupByTest**: Tests groupBy performance
- **PageRank**: Graph algorithm example

Example jar location in the container:
```
/opt/spark/examples/jars/spark-examples_2.12-3.5.0.jar
```

## Troubleshooting Checklist

- Operator running: `kubectl -n spark-operator get pods`
- CRD installed: `kubectl get crd sparkapplications.sparkoperator.k8s.io`
- ServiceAccount exists: `kubectl get sa spark`
- RBAC configured: `kubectl get role,rolebinding | grep spark`
- Image pullable: Check imagePullPolicy and secrets
- Resources available: `kubectl describe nodes`
