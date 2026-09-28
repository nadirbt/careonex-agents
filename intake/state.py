"""Typed intake state, built from intake/form_schema.v1.json.

The dialog agent (SOW Section 3, stage 3) fills this in turn by turn; the
post-call model (stage 6, "Wrap-up") reconciles it into the final record.
Every leaf field is a TrackedField, not a bare value, so the agent's
confidence and the eval harness's field-accuracy scoring have somewhere to
live -- see intake/README.md, "Why a confidence-per-field model."

Deliberately does NOT model consent -- see intake/consent.py and
intake/README.md for why that's a separate artifact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Optional


class Confidence(str, Enum):
    """How sure the agent is that a field's value is correct.

    UNASKED   -- never came up.
    STATED    -- the caller said it, agent has not read it back.
    CONFIRMED -- the agent read it back and the caller agreed.
    """

    UNASKED = "unasked"
    STATED = "stated"
    CONFIRMED = "confirmed"


@dataclass
class TrackedField:
    value: Optional[str] = None
    confidence: Confidence = Confidence.UNASKED
    source_turn: Optional[int] = None

    def set(self, value: str, *, confidence: Confidence = Confidence.STATED, turn: Optional[int] = None) -> None:
        self.value = value
        self.confidence = confidence
        self.source_turn = turn

    def confirm(self) -> None:
        if self.value is not None:
            self.confidence = Confidence.CONFIRMED

    @property
    def is_filled(self) -> bool:
        return self.value is not None

    def to_json(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "confidence": self.confidence.value,
            "source_turn": self.source_turn,
        }


def _tf() -> TrackedField:
    return field(default_factory=TrackedField)


# ---------------------------------------------------------------------------
# Sections, mirroring intake/form_schema.v1.json exactly (field names kept
# snake_case; original mixed-case labels like "Medicaid_MLTSS_plan" and
# "ADL_assistance_needed" are preserved as comments for traceability).
# ---------------------------------------------------------------------------


@dataclass
class IntakeMeta:
    """Section: intake."""

    name_or_title_of_person_taking_information: TrackedField = _tf()
    start_of_care_date: TrackedField = _tf()


@dataclass
class ClientInfo:
    """Section: client. first_name/last_name/phone are the form's only
    explicitly_required_fields -- see REQUIRED_FIELD_PATHS below."""

    first_name: TrackedField = _tf()
    last_name: TrackedField = _tf()
    phone: TrackedField = _tf()
    date_of_birth: TrackedField = _tf()
    gender: TrackedField = _tf()
    marital_status: TrackedField = _tf()
    care_type: TrackedField = _tf()
    address_line_1: TrackedField = _tf()
    address_line_2: TrackedField = _tf()
    city: TrackedField = _tf()
    state: TrackedField = _tf()
    zip_code: TrackedField = _tf()
    zip_code_verification: TrackedField = _tf()


@dataclass
class Contact:
    """One entry from the contacts section (information_provider,
    emergency_contact, or collateral_contact)."""

    contact_type: str
    name: TrackedField = _tf()
    phone: TrackedField = _tf()
    relationship: TrackedField = _tf()


def _default_contacts() -> dict[str, Contact]:
    return {
        "information_provider": Contact(contact_type="information_provider"),
        "emergency_contact": Contact(contact_type="emergency_contact"),
        "collateral_contact": Contact(contact_type="collateral_contact"),
    }


# The form's fixed set of selectable service types (services_and_referral.service_requested).
SERVICE_TYPES = ("PCS / CHHA", "Skilled Nursing (CBSN)", "Companion")


@dataclass
class ServicesAndReferral:
    """Section: services_and_referral."""

    service_requested: list[str] = field(default_factory=list)  # subset of SERVICE_TYPES
    requested_days_or_hours: TrackedField = _tf()
    requested_start_date: TrackedField = _tf()
    setting: TrackedField = _tf()
    special_skills_or_certifications_required: TrackedField = _tf()
    special_employer_policies_or_limitations: TrackedField = _tf()
    client_living_situation: TrackedField = _tf()
    languages_spoken: TrackedField = _tf()
    translator_needed: TrackedField = _tf()
    referral_source: TrackedField = _tf()
    reason_for_referral: TrackedField = _tf()
    physician_name: TrackedField = _tf()
    physician_specialty: TrackedField = _tf()
    physician_phone: TrackedField = _tf()

    def add_service(self, service: str) -> None:
        if service not in SERVICE_TYPES:
            raise ValueError(f"Unknown service type {service!r}; expected one of {SERVICE_TYPES}")
        if service not in self.service_requested:
            self.service_requested.append(service)


@dataclass
class MedicalInfo:
    """Section: medical."""

    past_medical_history: TrackedField = _tf()
    medical_or_nursing_diagnosis: TrackedField = _tf()
    medications: TrackedField = _tf()
    known_allergies: TrackedField = _tf()
    advance_directive_status: TrackedField = _tf()
    diet: TrackedField = _tf()
    emergency_priority_code: TrackedField = _tf()
    financial_or_insurance_type: TrackedField = _tf()
    medicaid_mltss_plan: TrackedField = _tf()  # form label: "Medicaid_MLTSS_plan"
    other_financial_information: TrackedField = _tf()


@dataclass
class FunctionalStatus:
    """Section: functional_status."""

    mobility: TrackedField = _tf()
    equipment_in_home: TrackedField = _tf()
    equipment_supplier: TrackedField = _tf()
    adl_assistance_needed: TrackedField = _tf()  # form label: "ADL_assistance_needed"
    iadl_assistance_needed: TrackedField = _tf()  # form label: "IADL_assistance_needed"
    transportation: TrackedField = _tf()
    vision: TrackedField = _tf()
    hearing: TrackedField = _tf()
    hearing_aids: TrackedField = _tf()
    speech: TrackedField = _tf()
    alert_awake_oriented_status: TrackedField = _tf()
    explanation_if_not_oriented: TrackedField = _tf()
    memory_issues: TrackedField = _tf()
    other_memory_information: TrackedField = _tf()
    incontinence_status: TrackedField = _tf()
    incontinence_type: TrackedField = _tf()
    current_services_in_place: TrackedField = _tf()
    explanation_of_current_services: TrackedField = _tf()


@dataclass
class Summary:
    """Section: summary. Populated by the post-call model (stage 6), not
    turn by turn during the call."""

    pertinent_information_for_level_of_care_appropriateness: TrackedField = _tf()
    notes: TrackedField = _tf()
    signature_or_title_of_person_taking_information: TrackedField = _tf()
    date: TrackedField = _tf()


@dataclass
class IntakeState:
    """The full record. One instance per call."""

    intake: IntakeMeta = field(default_factory=IntakeMeta)
    client: ClientInfo = field(default_factory=ClientInfo)
    contacts: dict[str, Contact] = field(default_factory=_default_contacts)
    services_and_referral: ServicesAndReferral = field(default_factory=ServicesAndReferral)
    medical: MedicalInfo = field(default_factory=MedicalInfo)
    functional_status: FunctionalStatus = field(default_factory=FunctionalStatus)
    summary: Summary = field(default_factory=Summary)

    # Free-text disposition for now -- see intake/README.md, "What's NOT
    # modeled yet." Replace with an Enum once a fixed list is confirmed.
    disposition: Optional[str] = None

    def to_json(self) -> dict[str, Any]:
        return _dataclass_to_json(self)

    def to_json_str(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_json(), indent=indent)


# Dotted paths the form marks as hard-required (explicitly_required_fields).
REQUIRED_FIELD_PATHS = ("client.first_name", "client.last_name", "client.phone")

# Dotted paths the form marks as required_information per section -- softer
# than REQUIRED_FIELD_PATHS: strongly desired, but a call can end without
# them (with an escalation/follow-up), unlike the three hard-required fields.
SECTION_REQUIRED_INFORMATION: dict[str, tuple[str, ...]] = {
    "intake": ("name_or_title_of_person_taking_information", "start_of_care_date"),
    "client": ("first_name", "last_name", "phone"),
    "summary": (
        "pertinent_information_for_level_of_care_appropriateness",
        "notes",
        "signature_or_title_of_person_taking_information",
        "date",
    ),
}


def _get_path(state: IntakeState, dotted_path: str) -> TrackedField:
    obj: Any = state
    for part in dotted_path.split("."):
        obj = getattr(obj, part)
    if not isinstance(obj, TrackedField):
        raise TypeError(f"{dotted_path} does not resolve to a TrackedField")
    return obj


def missing_hard_required(state: IntakeState) -> list[str]:
    """The 3 fields the form will not validate without (client.*)."""
    return [p for p in REQUIRED_FIELD_PATHS if not _get_path(state, p).is_filled]


def missing_section_required(state: IntakeState) -> dict[str, list[str]]:
    """Section-declared required_information that is still empty, by section."""
    missing: dict[str, list[str]] = {}
    for section, field_names in SECTION_REQUIRED_INFORMATION.items():
        section_obj = getattr(state, section)
        empty = [
            name
            for name in field_names
            if not getattr(section_obj, name).is_filled
        ]
        if empty:
            missing[section] = empty
    return missing


def completeness(state: IntakeState) -> float:
    """Fraction of all TrackedFields (excluding contacts) that are filled.

    A rough progress signal for the operator console's "confidence per
    field" display (SOW Section 3, application mock design) -- not a
    substitute for missing_hard_required / missing_section_required, which
    check specific required fields rather than an overall average.
    """
    all_fields = list(_iter_tracked_fields(state))
    if not all_fields:
        return 0.0
    filled = sum(1 for tf in all_fields if tf.is_filled)
    return filled / len(all_fields)


def _iter_tracked_fields(obj: Any):
    if isinstance(obj, TrackedField):
        yield obj
        return
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _iter_tracked_fields(value)
        return
    if is_dataclass(obj) and not isinstance(obj, type):
        for f in fields(obj):
            yield from _iter_tracked_fields(getattr(obj, f.name))


def _dataclass_to_json(obj: Any) -> Any:
    if isinstance(obj, TrackedField):
        return obj.to_json()
    if isinstance(obj, dict):
        return {k: _dataclass_to_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dataclass_to_json(v) for v in obj]
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _dataclass_to_json(getattr(obj, f.name)) for f in fields(obj)}
    return obj
