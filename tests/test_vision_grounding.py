import contextlib
import io
import json
from pathlib import Path

import pytest

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import execution_provider_catalog
from agent_evolution_protocol.coordination import vision_grounding
from agent_evolution_protocol.coordination.vision_grounding import (
    GroundingRefused,
    HttpGrounder,
    NullGrounder,
    Proposal,
    Region,
    StaticGrounder,
    admissible,
    agreement,
    disagreement_rate,
    from_normalised,
    require_provider,
)


ROOT = Path(__file__).parents[1]
CATALOG = ROOT / "examples" / "execution-provider-catalog.json"
WINDOW = Region(x=0, y=0, width=1920, height=1080)


def _proposal(region, confidence=0.9, target="composer"):
    return Proposal(target=target, region=region, confidence=confidence, source="test")


class TestNormalisedCoordinates:
    """The model answers on a 0-999 grid; out-of-range is a refusal, not a clamp."""

    def test_the_grid_is_converted_to_window_pixels(self):
        proposal = from_normalised("composer", x=0, y=0, width=999, height=999,
                                   window=WINDOW, confidence=0.8, source="test")
        assert proposal.region == Region(x=0, y=0, width=1920, height=1080)

    def test_the_middle_of_the_grid_is_the_middle_of_the_window(self):
        proposal = from_normalised("composer", x=500, y=500, width=10, height=10,
                                   window=WINDOW, confidence=0.8, source="test")
        centre_x, centre_y = proposal.region.centre()
        assert abs(centre_x - WINDOW.width // 2) <= 10
        assert abs(centre_y - WINDOW.height // 2) <= 10

    def test_a_value_past_the_grid_is_refused_rather_than_clamped(self):
        # Clamping 1200 to 999 would turn "the model answered a different
        # question" into a confident click in the corner of the window.
        with pytest.raises(GroundingRefused, match=r"within \[0, 999\]"):
            from_normalised("composer", x=1200, y=0, width=10, height=10,
                            window=WINDOW, confidence=0.9, source="test")
        with pytest.raises(GroundingRefused, match=r"within \[0, 999\]"):
            from_normalised("composer", x=0, y=-5, width=10, height=10,
                            window=WINDOW, confidence=0.9, source="test")

    def test_a_zero_sized_box_is_refused(self):
        with pytest.raises(GroundingRefused, match="positive"):
            from_normalised("composer", x=10, y=10, width=0, height=10,
                            window=WINDOW, confidence=0.9, source="test")

    def test_a_tiny_box_still_gets_at_least_one_pixel(self):
        proposal = from_normalised("composer", x=10, y=10, width=0.1, height=0.1,
                                   window=WINDOW, confidence=0.9, source="test")
        assert (proposal.region.width, proposal.region.height) == (1, 1)


class TestAdmissibility:
    def test_no_proposal_is_not_a_location(self):
        assert admissible(None, window=WINDOW) == (False, "NO_PROPOSAL")

    def test_a_proposal_without_a_region_is_not_a_location(self):
        bare = Proposal(target="composer", region=None, confidence=0.9, source="test")
        assert admissible(bare, window=WINDOW) == (False, "NO_REGION")

    def test_low_confidence_is_refused(self):
        weak = _proposal(Region(10, 10, 100, 40), confidence=0.2)
        assert admissible(weak, window=WINDOW) == (False, "LOW_CONFIDENCE")

    def test_the_confidence_threshold_is_inclusive(self):
        borderline = _proposal(Region(10, 10, 100, 40), confidence=0.5)
        assert admissible(borderline, window=WINDOW) == (True, "")

    def test_a_region_hanging_outside_the_window_is_refused(self):
        overhang = _proposal(Region(1800, 10, 400, 40))
        assert admissible(overhang, window=WINDOW) == (False, "OUTSIDE_WINDOW")

    def test_a_good_proposal_is_admissible_with_no_reason(self):
        assert admissible(_proposal(Region(700, 960, 500, 60)), window=WINDOW) == (True, "")


class TestAgreement:
    def test_the_same_box_agrees(self):
        reference = Region(700, 960, 500, 60)
        result = agreement(_proposal(reference), reference)
        assert result["agree"] is True
        assert result["iou"] == 1.0
        assert result["centre_inside"] is True

    def test_a_far_away_box_disagrees(self):
        reference = Region(700, 960, 500, 60)
        result = agreement(_proposal(Region(10, 10, 100, 40)), reference)
        assert result["agree"] is False
        assert result["iou"] == 0.0
        assert result["centre_inside"] is False

    def test_a_huge_box_that_swallows_the_control_is_not_agreement(self):
        # The failure IoU forgives: a predicted box covering the whole window
        # has a low IoU against a 500x60 composer, and its centre lands nowhere
        # near the control.  Both tests fail, so the pair is a real miss.
        reference = Region(700, 960, 500, 60)
        huge = _proposal(Region(0, 0, 1920, 1080))
        result = agreement(huge, reference)
        assert result["iou"] < 0.05
        assert result["centre_inside"] is False
        assert result["agree"] is False

    def test_a_missing_proposal_is_reported_not_scored_as_zero(self):
        result = agreement(None, Region(700, 960, 500, 60))
        assert result["agree"] is False
        assert result["reason"] == "MISSING_PROPOSAL"

    def test_a_missing_reference_is_reported_separately(self):
        result = agreement(_proposal(Region(700, 960, 500, 60)), None)
        assert result["reason"] == "MISSING_REFERENCE"


class TestDisagreementRate:
    def test_no_decided_rows_yield_no_rate(self):
        result = disagreement_rate([{"target": "composer", "agree": None}])
        assert result["decided"] == 0
        assert result["rate"] is None

    def test_having_no_opinion_is_not_counted_as_being_wrong(self):
        # "The grounder had no opinion" and "the grounder was wrong" are
        # different failures; only the second one argues against adopting it.
        result = disagreement_rate([
            {"target": "composer", "agree": True},
            {"target": "send", "agree": None},
            {"target": "attach", "agree": True},
        ])
        assert (result["decided"], result["disagreed"], result["rate"]) == (2, 0, 0.0)

    def test_disagreements_are_counted_and_named(self):
        result = disagreement_rate([
            {"target": "composer", "agree": False},
            {"target": "send", "agree": False},
            {"target": "attach", "agree": True},
            {"target": "menu", "agree": None},
        ])
        assert (result["decided"], result["disagreed"], result["rate"]) == (3, 2, 0.6667)
        assert result["disagreed_targets"] == ["composer", "send"]


class TestProviderGate:
    """A registry that only describes intent while the code runs anyway is worse
    than no registry, because it looks like governance."""

    @pytest.fixture
    def catalog(self, tmp_path):
        target = tmp_path / "catalog.json"
        target.write_text(CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
        return target

    def test_an_unregistered_provider_is_refused(self, catalog):
        with pytest.raises(GroundingRefused, match="not registered"):
            require_provider("not_a_provider", "probed", str(catalog))

    def test_a_candidate_cannot_ground_anything(self, catalog):
        with pytest.raises(GroundingRefused, match="needs at least probed"):
            require_provider("mage_vl_cowork", "probed", str(catalog))

    def test_a_probed_row_may_ground(self, catalog):
        execution_provider_catalog.promote(
            catalog, "mage_vl_cowork", to="probed",
            evidence={"probe_command": "curl /health", "probe_at": "2026-10-31T13:00:00Z",
                      "probe_result": "pass"})
        row = require_provider("mage_vl_cowork", "probed", str(catalog))
        assert row["status"] == "probed"

    def test_constructing_a_grounder_is_the_permission_check(self, catalog):
        # The gate is in __init__, so there is no way to hold a usable
        # model-backed grounder whose row was never probed.
        with pytest.raises(GroundingRefused, match="needs at least probed"):
            HttpGrounder("http://127.0.0.1:30000", catalog_path=str(catalog))


class TestHttpGrounderParsing:
    """The transport is injected, so the parser is testable without a GPU."""

    @pytest.fixture
    def grounder(self, tmp_path):
        catalog = tmp_path / "catalog.json"
        catalog.write_text(CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
        execution_provider_catalog.promote(
            catalog, "mage_vl_cowork", to="probed",
            evidence={"probe_command": "curl /health", "probe_at": "2026-10-31T13:00:00Z",
                      "probe_result": "pass"})
        replies = {}

        def post(url, payload):
            return replies["next"]

        grounder = HttpGrounder("http://127.0.0.1:30000", catalog_path=str(catalog), post=post)
        grounder.replies = replies
        return grounder

    def _ask(self, grounder, reply):
        grounder.replies["next"] = reply
        return grounder.ground({"target": "composer", "window": WINDOW, "image_data": ""})

    def test_a_plain_json_reply_becomes_a_proposal(self, grounder):
        proposal = self._ask(grounder, {"text": '{"x": 364, "y": 888, "width": 260, '
                                                '"height": 55, "confidence": 0.91, "enabled": true}'})
        assert proposal.confidence == 0.91
        assert proposal.enabled is True
        assert proposal.region.width > 0

    def test_prose_around_the_object_is_tolerated(self, grounder):
        proposal = self._ask(grounder, {"text": 'I see it here: {"x": 364, "y": 888, '
                                                '"width": 260, "height": 55, "confidence": 0.8} '
                                                'hope that helps'})
        assert proposal.region is not None
        assert proposal.note == ""

    def test_a_chat_completions_envelope_is_read(self, grounder):
        proposal = self._ask(grounder, {"choices": [{"message": {
            "content": '{"x": 1, "y": 1, "width": 100, "height": 100, "confidence": 0.7}'}}]})
        assert proposal.region is not None

    def test_an_unparsed_reply_is_no_opinion_rather_than_an_error(self, grounder):
        proposal = self._ask(grounder, {"text": "the composer is at the bottom"})
        assert proposal.region is None
        assert proposal.note == "unparsed reply"

    def test_a_transport_failure_is_no_opinion_rather_than_a_crash(self, grounder):
        def boom(url, payload):
            raise TimeoutError("no route to host")

        grounder._post = boom
        proposal = grounder.ground({"target": "composer", "window": WINDOW})
        assert proposal.region is None
        assert "transport failed: TimeoutError" == proposal.note

    def test_out_of_range_coordinates_are_rejected_with_the_reason_kept(self, grounder):
        proposal = self._ask(grounder, {"text": '{"x": 1500, "y": 10, "width": 10, '
                                                '"height": 10, "confidence": 0.99}'})
        assert proposal.region is None
        assert proposal.note.startswith("rejected:")


def test_the_null_grounder_has_no_opinion_instead_of_a_cheap_one():
    assert NullGrounder().ground({"target": "composer", "window": WINDOW}) is None


def test_the_static_grounder_replays_a_recorded_answer():
    recorded = _proposal(Region(700, 960, 500, 60))
    grounder = StaticGrounder({"composer": recorded})
    assert grounder.ground({"target": "composer"}) is recorded
    assert grounder.ground({"target": "send"}) is None
    assert grounder.calls == 2


def _run(argv):
    args = cli.build_parser().parse_args(argv)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        args.func(args)
    return json.loads(buffer.getvalue())


def test_the_command_reports_which_registered_rows_may_ground():
    payload = _run(["coord-vision", "providers", "--catalog", str(CATALOG)])
    rows = {row["id"]: row for row in payload["providers"]}
    assert rows["mage_vl_cowork"]["usable_for_grounding"] is False
    assert all(row["status"] == "candidate" for row in payload["providers"])


def test_the_command_without_a_grounder_says_so_instead_of_inventing_a_box():
    payload = _run(["coord-vision", "ground", "--window", "1920x1080",
                    "--target", "composer", "--reference", "700,960,500,60"])
    assert payload["proposal"] is None
    assert payload["admissible"] is False
    assert payload["refused_because"] == "NO_PROPOSAL"
    assert payload["agreement"]["reason"] == "MISSING_PROPOSAL"


def test_the_command_without_a_reference_does_not_score_itself():
    payload = _run(["coord-vision", "ground", "--window", "1920x1080"])
    assert payload["agreement"] == {"agree": None, "reason": "MISSING_REFERENCE"}


def test_a_candidate_provider_cannot_be_used_by_the_command(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(cli.ProtocolError, match="needs at least probed"):
        _run(["coord-vision", "ground", "--window", "1920x1080",
              "--endpoint", "http://127.0.0.1:30000", "--catalog", str(catalog)])


def test_a_malformed_window_is_a_protocol_error_not_a_traceback():
    with pytest.raises(cli.ProtocolError, match="WIDTHxHEIGHT"):
        _run(["coord-vision", "ground", "--window", "1920"])
