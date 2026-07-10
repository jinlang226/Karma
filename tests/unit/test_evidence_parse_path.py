"""Kubernetes API request-path parsing for evidence trace facts (incl. SS-6)."""
from karma.evidence import _parse_api_path


def test_namespaced_resource_paths():
    assert _parse_api_path("/api/v1/namespaces/ns/pods") == ("pods", "ns", "")
    assert _parse_api_path("/api/v1/namespaces/ns/configmaps/c") == ("configmaps", "ns", "c")
    assert _parse_api_path("/apis/apps/v1/namespaces/ns/deployments/web") == ("deployments", "ns", "web")


def test_cluster_scoped_paths():
    assert _parse_api_path("/api/v1/nodes") == ("nodes", "", "")
    assert _parse_api_path("/api/v1/nodes/node-1") == ("nodes", "", "node-1")


def test_namespace_object_paths_ss6():
    # SS-6: an op on a namespace OBJECT keeps resource="namespaces" and the name,
    # instead of losing both by reading the object name as the containing namespace.
    assert _parse_api_path("/api/v1/namespaces/foo") == ("namespaces", "", "foo")
    assert _parse_api_path("/api/v1/namespaces/foo?x=1") == ("namespaces", "", "foo")
    # create/list of the namespaces collection (no object name) already parsed fine
    assert _parse_api_path("/api/v1/namespaces") == ("namespaces", "", "")


def test_query_string_and_empty():
    assert _parse_api_path("/api/v1/namespaces/ns/secrets/s?x=1")[0] == "secrets"
    assert _parse_api_path("") == ("", "", "")
