"""Vision grounding as an advisory proposal, never as a success claim.

A vision model can say where a control probably is.  It cannot say the input was
typed, the message was sent, or the agent answered -- those stay with the
evidence ladder in ``commander``/``ui_fallback``.  This module exists to keep
that boundary explicit, so wiring a model in cannot quietly become a second
source of truth about delivery.

Two rules are load-bearing:

1. **A proposal is not an observation.** ``Proposal`` carries a confidence and a
   source; ``admissible()`` refuses low-confidence or out-of-window proposals
   instead of letting a guess reach the input layer.
2. **A model-backed grounder cannot be built until the catalog says it was
   probed.** ``require_provider`` reads the same status field that
   ``execution_provider_catalog.promote`` refuses to advance without evidence,
   so "we registered it" and "we may run it" are not the same thing.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from agent_evolution_protocol.coordination import execution_provider_catalog

COORDINATE_SCALE = 999
DEFAULT_MIN_CONFIDENCE = 0.5


class GroundingRefused(RuntimeError):
    """The provider may not be used yet, or the proposal is not admissible."""


@dataclass(frozen=True)
class Region:
    """A window-relative rectangle in pixels."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("a region needs a positive size")
        if self.x < 0 or self.y < 0:
            raise ValueError("a region cannot start outside the window")

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def centre(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def intersection(self, other: "Region") -> int:
        width = min(self.right, other.right) - max(self.x, other.x)
        height = min(self.bottom, other.bottom) - max(self.y, other.y)
        return max(0, width) * max(0, height)

    def iou(self, other: "Region") -> float:
        overlap = self.intersection(other)
        if not overlap:
            return 0.0
        union = self.width * self.height + other.width * other.height - overlap
        return overlap / union if union else 0.0

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class Proposal:
    """What a grounder thinks it found.  Advisory only."""

    target: str
    region: Region | None
    confidence: float
    source: str
    enabled: bool | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"target": self.target, "source": self.source,
                "confidence": round(self.confidence, 3),
                "region": self.region.as_dict() if self.region else None,
                "enabled": self.enabled, "note": self.note}


def from_normalised(target: str, *, x: float, y: float, width: float, height: float,
                    window: Region, confidence: float, source: str,
                    enabled: bool | None = None, note: str = "") -> Proposal:
    """Convert the model's ``[0, 999]`` coordinates into window pixels.

    Refusing out-of-range values here is deliberate: a model that answers 1200 is
    not slightly wrong about the layout, it is answering a different question,
    and clamping it would turn that into a confident click somewhere arbitrary.
    """
    values = (x, y, width, height)
    if any(value < 0 or value > COORDINATE_SCALE for value in values):
        raise GroundingRefused(f"normalised coordinates must be within [0, {COORDINATE_SCALE}]")
    if width <= 0 or height <= 0:
        raise GroundingRefused("normalised size must be positive")
    scale_x, scale_y = window.width / COORDINATE_SCALE, window.height / COORDINATE_SCALE
    region = Region(x=round(x * scale_x), y=round(y * scale_y),
                    width=max(1, round(width * scale_x)),
                    height=max(1, round(height * scale_y)))
    return Proposal(target=target, region=region, confidence=float(confidence),
                    source=source, enabled=enabled, note=note)


def admissible(proposal: Proposal | None, *, window: Region,
               min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> tuple[bool, str]:
    """Would this proposal be safe to act on?  Empty reason means yes."""
    if proposal is None:
        return False, "NO_PROPOSAL"
    if proposal.region is None:
        return False, "NO_REGION"
    if proposal.confidence < min_confidence:
        return False, "LOW_CONFIDENCE"
    if not (0 <= proposal.region.x and 0 <= proposal.region.y
            and proposal.region.right <= window.width
            and proposal.region.bottom <= window.height):
        return False, "OUTSIDE_WINDOW"
    return True, ""


def agreement(proposal: Proposal | None, reference: Region | None,
              *, min_iou: float = 0.3) -> dict[str, Any]:
    """Compare one proposal with the answer the deterministic path gave.

    Both a box overlap and a centre test are reported, because a small control
    inside a large predicted box is a real miss that IoU alone forgives.
    """
    if proposal is None or proposal.region is None or reference is None:
        return {"agree": False, "iou": 0.0, "centre_inside": False,
                "reason": "MISSING_PROPOSAL" if proposal is None or proposal.region is None
                else "MISSING_REFERENCE"}
    iou = proposal.region.iou(reference)
    centre_inside = reference.contains(*proposal.region.centre())
    return {"agree": bool(iou >= min_iou or centre_inside), "iou": round(iou, 3),
            "centre_inside": centre_inside, "reason": ""}


def disagreement_rate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise a recorded comparison set.

    ``rows`` is a list of ``{"target": str, "agree": bool}``.  Missing rows are
    counted separately rather than folded into the rate, because "the grounder
    had no opinion" and "the grounder was wrong" are different failures and the
    second one is the only one that argues against adopting it.
    """
    decided = [row for row in rows if isinstance(row.get("agree"), bool)]
    if not decided:
        return {"decided": 0, "disagreed": 0, "rate": None,
                "note": "no decided rows; nothing to conclude"}
    disagreed = [row for row in decided if row["agree"] is False]
    return {"decided": len(decided), "disagreed": len(disagreed),
            "rate": round(len(disagreed) / len(decided), 4),
            "disagreed_targets": sorted({str(row.get("target", "")) for row in disagreed})}


class NullGrounder:
    """A grounder with no opinion, so the caller must fall back on purpose."""

    name = "null"

    def ground(self, request: dict[str, Any]) -> Proposal | None:
        return None


class StaticGrounder:
    """Deterministic grounder for tests and for replaying recorded answers."""

    def __init__(self, table: dict[str, Proposal | None]):
        self.name = "static"
        self.table = dict(table)
        self.calls = 0

    def ground(self, request: dict[str, Any]) -> Proposal | None:
        self.calls += 1
        return self.table.get(str(request.get("target", "")))


def require_provider(provider_id: str, minimum: str = "probed",
                     path: str | None = None) -> dict[str, Any]:
    """Refuse to construct a model-backed grounder until the row was probed.

    The catalog gate and the runtime check are the same field on purpose.  A
    registry that only describes intent while the code runs anyway is worse than
    no registry, because it looks like governance.
    """
    catalog = execution_provider_catalog.load(path)
    row = next((item for item in catalog["providers"] if item["id"] == provider_id), None)
    if row is None:
        raise GroundingRefused(f"{provider_id} is not registered in the provider catalog")
    order = execution_provider_catalog.STATUS_ORDER
    if row["status"] not in order:
        raise GroundingRefused(f"{provider_id} is {row['status']}; it cannot ground anything")
    if order.index(row["status"]) < order.index(minimum):
        raise GroundingRefused(
            f"{provider_id} is {row['status']}; grounding needs at least {minimum}. "
            "Probe it and record the evidence with `aep coord-providers promote`.")
    return row


GROUNDING_PROMPT = (
    "You are looking at a screenshot of one application window of size "
    "{width}x{height}. Locate the {target} and answer with a single JSON object and "
    'nothing else: {{"x": int, "y": int, "width": int, "height": int, '
    '"confidence": float, "enabled": bool}}. Coordinates use a 0-999 grid over the '
    "window, origin top-left. If the target is not visible, answer "
    '{{"x": 0, "y": 0, "width": 0, "height": 0, "confidence": 0.0}}.'
)


class HttpGrounder:
    """Ask a served model where a control is.  Gated, and parse-only-by-contract.

    The transport is injected so the parser is testable without a GPU or a
    network.  ``post(url, payload) -> dict`` is the whole seam; the default
    implementation is the one SGLang call this needs.
    """

    name = "http"

    def __init__(self, endpoint: str, *, provider_id: str = "mage_vl_cowork",
                 model: str = "Mage-VL-Cowork", post: Callable[[str, dict], dict] | None = None,
                 catalog_path: str | None = None):
        # Constructing this object is the permission check.
        self.row = require_provider(provider_id, "probed", catalog_path)
        self.provider_id = provider_id
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self._post = post or _http_post
        self.calls = 0

    def ground(self, request: dict[str, Any]) -> Proposal | None:
        window: Region = request["window"]
        target = str(request.get("target", "control"))
        prompt = GROUNDING_PROMPT.format(width=window.width, height=window.height, target=target)
        payload = {"model": self.model, "prompt": prompt,
                   "image_data": request.get("image_data", ""),
                   "max_new_tokens": 128, "temperature": 0.0}
        self.calls += 1
        try:
            reply = self._post(f"{self.endpoint}/generate", payload)
        except Exception as exc:  # noqa: BLE001 - a transport failure is "no opinion"
            return Proposal(target=target, region=None, confidence=0.0, source=self.name,
                            note=f"transport failed: {type(exc).__name__}")
        answer = _first_json_object(_reply_text(reply))
        if not isinstance(answer, dict):
            return Proposal(target=target, region=None, confidence=0.0, source=self.name,
                            note="unparsed reply")
        try:
            return from_normalised(
                target, x=float(answer.get("x", -1)), y=float(answer.get("y", -1)),
                width=float(answer.get("width", 0)), height=float(answer.get("height", 0)),
                window=window, confidence=float(answer.get("confidence", 0.0)),
                source=self.name, enabled=answer.get("enabled") if isinstance(answer.get("enabled"), bool) else None,
                note="")
        except (GroundingRefused, TypeError, ValueError) as exc:
            return Proposal(target=target, region=None, confidence=0.0, source=self.name,
                            note=f"rejected: {exc}")


def _http_post(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - local endpoint
        return json.loads(response.read().decode("utf-8"))


def _reply_text(reply: Any) -> str:
    """Accept the shapes a chat/completions or /generate endpoint may return."""
    if isinstance(reply, str):
        return reply
    if isinstance(reply, dict):
        for key in ("text", "output", "generated_text"):
            value = reply.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, list) and value and isinstance(value[0], str):
                return value[0]
        choices = reply.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            if isinstance(message.get("content"), str):
                return message["content"]
    return ""


def _first_json_object(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text or ""):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None
