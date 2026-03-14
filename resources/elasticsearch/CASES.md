# Elasticsearch Lifecycle Test Cases

Each case is a no-operator benchmark using native Kubernetes resources.

| # | Case | Type | Purpose |
|---|------|------|---------|
| 1 | deploy-core-cluster | Core | Create Services/StatefulSet/PDB/ConfigMaps |
| 2 | bootstrap-initial-master-nodes | Bootstrap | Fix initial master discovery |
| 3 | seed-hosts-repair | Discovery | Update seed hosts configmap |
| 4 | internal-http-service-drift | Service | Repair internal HTTP Service |
| 5 | secure-http-ingress | Security/Networking | Enable HTTP TLS and expose via ingress-nginx |
| 6 | enable-http-tls (deprecated) | Security | Merged into secure-http-ingress |
| 7 | rotate-http-certs | Security | Rotate HTTP certificates |
| 8 | transport-additional-ca-trust | Security | Add transport CA trust |
| 9 | snapshot-repo-setup | Security | Configure secure settings and snapshots |
| 10 | file-realm-user-roles-merge | Security | Merge file realm and roles |
| 11 | rotate-elastic-password | Security | Rotate elastic user password |
| 12 | scale-up-new-nodeset | Scale | Add new data nodeset |
| 13 | safe-downscale-with-shard-migration | Scale | Downscale to 1 node, migrate shards, and clean orphan PVCs |
| 14 | master-downscale-voting-exclusions | Scale | Downscale masters with voting exclusions |
| 15 | rolling-upgrade-minor | Upgrade | Rolling minor version upgrade |
| 16 | full-restart-upgrade-nonha | Upgrade | Full restart upgrade (non-HA) |
| 17 | pvc-expansion-sset-recreate | Storage | Expand PVCs and recreate StatefulSet |
| 18 | pvc-gc-after-downscale (deprecated) | Storage | Merged into safe-downscale-with-shard-migration |
| 19 | expose-http-ingress (deprecated) | Networking | Merged into secure-http-ingress |
| 20 | stack-monitoring-sidecars | Observability | Add Metricbeat/Filebeat sidecars |
| 21 | transform-node-enable | Transform | Add transform nodes and start job |
| 22 | transform-job-recovery | Transform | Recover a failed transform job |
