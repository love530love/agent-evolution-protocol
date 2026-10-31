import contextlib
import io
import json
from pathlib import Path

import pytest

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import execution_provider_catalog


ROOT = Path(__file__).parents[1]


def test_catalog_is_valid_and_contains_all_execution_layers():
    catalog = execution_provider_catalog.load(ROOT / "examples" / "execution-provider-catalog.json")
    assert catalog["provenance_policy"]["default_distribution_mode"] == "reference-only"
    layers = {row["layer"] for row in catalog["providers"]}
    assert {"browser", "desktop_visual", "terminal_execution", "code_engineering", "mobile", "reverse_engineering"} <= layers
    assert all(row["status"] == "candidate" for row in catalog["providers"])


def test_catalog_inventory_is_read_only_and_filterable():
    path = ROOT / "examples" / "execution-provider-catalog.json"
    rows = execution_provider_catalog.inventory(path, layer="browser")
    assert {row["id"] for row in rows} >= {"browser_use", "skyvern", "lavague"}
    with pytest.raises(ValueError, match="invalid provider status"):
        execution_provider_catalog.inventory(path, status="trusted")


def test_catalog_rejects_duplicate_provider_ids(tmp_path):
    data = json.loads((ROOT / "examples" / "execution-provider-catalog.json").read_text(encoding="utf-8"))
    data["providers"].append(dict(data["providers"][0]))
    with pytest.raises(ValueError, match="unique"):
        execution_provider_catalog.validate_catalog(data)


def test_catalog_rejects_unverified_distribution_policy():
    data = json.loads((ROOT / "examples" / "execution-provider-catalog.json").read_text(encoding="utf-8"))
    data["provenance_policy"]["default_distribution_mode"] = "vendored"
    with pytest.raises(ValueError, match="reference-only"):
        execution_provider_catalog.validate_catalog(data)


def test_the_shipped_catalog_matches_its_published_schema():
    # The Python validator and the JSON schema drifted apart: the catalog used
    # integration "vision-service" and a "license_note" field while the schema
    # allowed neither, and no test ever read the schema.  Two standards, and
    # the unenforced one was the one being shipped.
    jsonschema = pytest.importorskip("jsonschema", reason="schema validation needs the test extra")
    schema = json.loads((ROOT / "schemas" / "execution-provider-catalog-v1.schema.json").read_text(encoding="utf-8"))
    data = json.loads((ROOT / "examples" / "execution-provider-catalog.json").read_text(encoding="utf-8"))
    jsonschema.validate(data, schema)


def test_catalog_rejects_values_the_schema_would_reject():
    data = json.loads((ROOT / "examples" / "execution-provider-catalog.json").read_text(encoding="utf-8"))
    data["providers"][0]["layer"] = "magic"
    with pytest.raises(ValueError, match="invalid provider layer"):
        execution_provider_catalog.validate_catalog(data)
    data = json.loads((ROOT / "examples" / "execution-provider-catalog.json").read_text(encoding="utf-8"))
    data["providers"][0]["integration"] = "telepathy"
    with pytest.raises(ValueError, match="invalid provider integration"):
        execution_provider_catalog.validate_catalog(data)
    data = json.loads((ROOT / "examples" / "execution-provider-catalog.json").read_text(encoding="utf-8"))
    data["providers"][0]["trust_me"] = True
    with pytest.raises(ValueError, match="undeclared fields"):
        execution_provider_catalog.validate_catalog(data)


def test_mage_is_registered_as_an_untrusted_desktop_visual_candidate():
    catalog = execution_provider_catalog.load(ROOT / "examples" / "execution-provider-catalog.json")
    row = next(item for item in catalog["providers"] if item["id"] == "mage_vl_cowork")
    assert (row["layer"], row["status"]) == ("desktop_visual", "candidate")
    # The acceptance criteria have to be measurable, not aspirational: each one
    # names something a probe can produce and a reviewer can check.
    assert any("/health" in item for item in row["acceptance"])
    assert any("disagreement rate" in item for item in row["acceptance"])
    assert any("bash" in item for item in row["acceptance"])
    assert any("trust_remote_code" in item for item in row["limitations"])


def test_the_reverse_engineering_rows_record_what_makes_them_dangerous():
    # A row that only lists strengths is marketing.  These two tools both reach
    # a licensed, write-capable product, so the limits have to be in the file
    # that the promotion gate reads, not only in a chat message.
    catalog = execution_provider_catalog.load(ROOT / "examples" / "execution-provider-catalog.json")
    rows = {row["id"]: row for row in catalog["providers"]}
    rea, ida = rows["rea"], rows["ida_pro_mcp"]
    assert (rea["layer"], ida["layer"]) == ("reverse_engineering", "reverse_engineering")
    assert (rea["status"], ida["status"]) == ("candidate", "candidate")
    # rea-agents is an npm package; the previous entry claimed a python runtime.
    assert "node" in rea["runtime"] and "python" not in rea["runtime"]
    assert any("Node ^22.23.4" in item for item in rea["limitations"])
    assert any("crash dumps" in item for item in rea["limitations"])
    # ida-pro-mcp is a write-capable shell into a commercial product.
    assert any("py_eval" in item for item in ida["limitations"])
    assert any("IDA Free is not supported" in item for item in ida["limitations"])
    assert any("deprecated" in item for item in ida["limitations"])
    assert any("read-only subset" in item for item in ida["acceptance"])
    for row in (rea, ida):
        assert "MIT" in row["license_note"]
        assert row["source_note"]


class TestPromotionGate:
    """Promotion is a gate: one step at a time, only with that step's evidence."""

    @pytest.fixture
    def catalog(self, tmp_path):
        source = ROOT / "examples" / "execution-provider-catalog.json"
        target = tmp_path / "catalog.json"
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return target

    PROBE = {"probe_command": "curl /health", "probe_at": "2026-10-31T13:00:00Z",
             "probe_result": "pass"}

    def test_promotion_needs_the_evidence_of_that_step(self, catalog):
        with pytest.raises(ValueError, match="needs evidence"):
            execution_provider_catalog.promote(catalog, "mage_vl_cowork", to="probed", evidence={})
        with pytest.raises(ValueError, match="probe_result"):
            execution_provider_catalog.promote(
                catalog, "mage_vl_cowork", to="probed",
                evidence={k: v for k, v in self.PROBE.items() if k != "probe_result"})

    def test_a_failed_probe_is_not_a_promotion(self, catalog):
        # Keeping the finding is the point: "probe_result: fail" is a result, not
        # a status.  The row stays a candidate, or it is retired.
        with pytest.raises(ValueError, match="not a promotion"):
            execution_provider_catalog.promote(
                catalog, "mage_vl_cowork", to="probed",
                evidence={**self.PROBE, "probe_result": "fail"})
        row = next(item for item in execution_provider_catalog.load(catalog)["providers"]
                   if item["id"] == "mage_vl_cowork")
        assert row["status"] == "candidate"

    def test_a_row_cannot_skip_a_step(self, catalog):
        with pytest.raises(ValueError, match="only promotion from here is probed"):
            execution_provider_catalog.promote(
                catalog, "mage_vl_cowork", to="session-accepted",
                evidence={"session_event_id": "E", "session_receipt": "r", "session_at": "t"})

    def test_a_promotion_is_recorded_in_the_journal(self, catalog):
        result = execution_provider_catalog.promote(
            catalog, "mage_vl_cowork", to="probed", evidence=self.PROBE,
            now="2026-10-31T13:00:00Z")
        assert (result["from"], result["to"]) == ("candidate", "probed")
        row = next(item for item in execution_provider_catalog.load(catalog)["providers"]
                   if item["id"] == "mage_vl_cowork")
        assert row["status"] == "probed"
        entries = execution_provider_catalog.history(catalog, "mage_vl_cowork")
        assert len(entries) == 1
        assert entries[0]["evidence"]["probe_command"] == "curl /health"

    def test_a_retired_row_cannot_be_promoted_back(self, catalog):
        execution_provider_catalog.retire(catalog, "mage_vl_cowork", reason="probe failed: latency")
        with pytest.raises(ValueError, match="retired"):
            execution_provider_catalog.promote(catalog, "mage_vl_cowork", to="probed",
                                               evidence=self.PROBE)
        # Retiring twice is a no-op rather than an error: idempotence matters
        # when the caller is an automated sweep.
        again = execution_provider_catalog.retire(catalog, "mage_vl_cowork", reason="again")
        assert again["unchanged"] is True

    def test_retirement_needs_a_reason(self, catalog):
        with pytest.raises(ValueError, match="reason"):
            execution_provider_catalog.retire(catalog, "mage_vl_cowork", reason="  ")

    def test_an_unknown_provider_is_refused(self, catalog):
        with pytest.raises(ValueError, match="unknown provider"):
            execution_provider_catalog.promote(catalog, "not_a_provider", to="probed",
                                               evidence=self.PROBE)


def _run_cli(argv):
    args = cli.build_parser().parse_args(argv)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        cli.coord_providers(args)
    return json.loads(buffer.getvalue())


def test_the_operator_command_can_list_and_filter():
    payload = _run_cli(["coord-providers", "list", "--layer", "desktop_visual"])
    ids = {row["id"] for row in payload["providers"]}
    assert "mage_vl_cowork" in ids
    assert all(row["layer"] == "desktop_visual" for row in payload["providers"])


def test_the_operator_command_turns_a_refusal_into_a_protocol_error(tmp_path):
    # A refusal is the normal outcome; it must not surface as a traceback with
    # exit 0.  main() maps ProtocolError to stderr + exit 2.
    source = ROOT / "examples" / "execution-provider-catalog.json"
    target = tmp_path / "catalog.json"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(cli.ProtocolError, match="needs evidence"):
        _run_cli(["coord-providers", "promote", "--id", "mage_vl_cowork",
                  "--to", "probed", "--catalog", str(target)])
    with pytest.raises(cli.ProtocolError, match="key=value"):
        _run_cli(["coord-providers", "promote", "--id", "mage_vl_cowork",
                  "--to", "probed", "--evidence", "not-a-pair",
                  "--catalog", str(target)])


def test_the_operator_command_records_evidence(tmp_path):
    source = ROOT / "examples" / "execution-provider-catalog.json"
    target = tmp_path / "catalog.json"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    payload = _run_cli(["coord-providers", "promote", "--id", "mage_vl_cowork",
                        "--to", "probed", "--catalog", str(target),
                        "--evidence", "probe_command=curl /health",
                        "probe_at=2026-10-31T13:00:00Z", "probe_result=pass"])
    assert payload["to"] == "probed"
    history = _run_cli(["coord-providers", "history", "--id", "mage_vl_cowork",
                        "--catalog", str(target)])
    assert history["history"][0]["evidence"]["probe_result"] == "pass"
