# Elasticsearch Operator General Notes

Common defaults in this suite:
- Namespace: elasticsearch
- Cluster name: es-cluster
- Default NodeSet: default
- Image: docker.elastic.co/elasticsearch/elasticsearch:8.11.1 (unless stated otherwise)
- HTTP port: 9200
- Transport port: 9300

Common services:
- Transport (headless): es-cluster-es-transport
- HTTP external: es-cluster-es-http
- HTTP internal: es-cluster-es-internal-http
- Headless per nodeset: es-cluster-es-<nodeset>

Common secrets/configmaps:
- HTTP certs internal: es-cluster-es-http-certs-internal
- HTTP certs public: es-cluster-es-http-certs-public
- Transport certs (per nodeset): <sset>-es-transport-certs
- Elastic user secret: es-cluster-es-elastic-user
- File realm + roles: es-cluster-es-xpack-file-realm
- Secure settings: es-cluster-es-secure-settings
- File settings: es-cluster-es-file-settings
- Scripts configmap: es-cluster-es-scripts
- Seed hosts configmap: es-cluster-es-unicast-hosts
- Default PDB: es-cluster-es-default

Useful commands:
```bash
kubectl -n elasticsearch get pods,svc,sts,pdb,cm,secret,pvc
kubectl -n elasticsearch logs es-cluster-es-default-0
curl -k -u elastic:$ELASTIC_PASSWORD https://es-cluster-es-http.elasticsearch.svc:9200/_cluster/health
curl -k -u elastic:$ELASTIC_PASSWORD https://es-cluster-es-http.elasticsearch.svc:9200/_cat/nodes?h=name,roles,ip
```

Common ES APIs:
- Cluster health: GET /_cluster/health
- Nodes: GET /_cat/nodes
- Shards: GET /_cat/shards
- Settings: GET /_cluster/settings
- Allocation excludes: PUT /_cluster/settings
- Voting exclusions: POST /_cluster/voting_config_exclusions
- Clear voting exclusions: DELETE /_cluster/voting_config_exclusions
- Transforms: GET /_transform/<id>/_stats
