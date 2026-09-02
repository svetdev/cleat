"""sarif — results from any scanner that speaks SARIF, as located findings.

Duplication, dead code, escapes, security findings: every serious scanner
emits SARIF, and a result is exactly the shape a site-keyed ratchet wants —
a rule, a message, a file and a line. `results()` yields (repo-relative
path, line, rule id, message) for every result in every run.
"""

import os


def _uri_to_path(location, run, base_dir):
    artifact = location.get("physicalLocation", {}).get("artifactLocation", {})
    uri = artifact.get("uri", "")
    base_id = artifact.get("uriBaseId")
    bases = run.get("originalUriBaseIds", {})
    if base_id and base_id in bases:
        uri = bases[base_id].get("uri", "").rstrip("/") + "/" + uri
    if uri.startswith("file://"):
        uri = uri[len("file://"):]
    if os.path.isabs(uri):
        return os.path.relpath(uri, base_dir)
    return uri


def results(report, base_dir):
    for run in report.get("runs", []):
        for result in run.get("results", []):
            rule = result.get("ruleId") or result.get("rule", {}).get("id") or "?"
            message = (result.get("message") or {}).get("text", "").strip()
            locations = result.get("locations") or [{}]
            region = locations[0].get("physicalLocation", {}).get("region", {})
            yield _uri_to_path(locations[0], run, base_dir), int(region.get("startLine", 0)), rule, message
