import json
import unittest
from pathlib import Path

from intake.consent import CallConsent, OpeningOutcome
from intake.state import (
    REQUIRED_FIELD_PATHS,
    SECTION_REQUIRED_INFORMATION,
    Confidence,
    IntakeState,
    SERVICE_TYPES,
    completeness,
    missing_hard_required,
    missing_section_required,
)

FORM_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "intake" / "form_schema.v1.json"


class FormSchemaFileTests(unittest.TestCase):
    """The versioned raw form -- make sure it's still valid JSON with the
    shape state.py assumes."""

    def test_form_schema_is_valid_json(self):
        raw = json.loads(FORM_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertIn("sections", raw)
        self.assertIn("explicitly_required_fields", raw)

    def test_explicitly_required_fields_match_state_py(self):
        raw = json.loads(FORM_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(tuple(raw["explicitly_required_fields"]), REQUIRED_FIELD_PATHS)

    def test_no_document_uploads_required(self):
        raw = json.loads(FORM_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(raw["documents_or_uploads_required"], [])


class IntakeStateTests(unittest.TestCase):
    def test_new_state_has_nothing_filled(self):
        state = IntakeState()
        self.assertEqual(completeness(state), 0.0)
        self.assertEqual(missing_hard_required(state), list(REQUIRED_FIELD_PATHS))

    def test_setting_a_field_updates_confidence_and_completeness(self):
        state = IntakeState()
        state.client.first_name.set("Maria", turn=3)
        self.assertEqual(state.client.first_name.value, "Maria")
        self.assertEqual(state.client.first_name.confidence, Confidence.STATED)
        self.assertEqual(state.client.first_name.source_turn, 3)
        self.assertGreater(completeness(state), 0.0)

    def test_confirm_upgrades_confidence(self):
        state = IntakeState()
        state.client.phone.set("555-0100")
        state.client.phone.confirm()
        self.assertEqual(state.client.phone.confidence, Confidence.CONFIRMED)

    def test_confirm_on_empty_field_is_a_noop(self):
        state = IntakeState()
        state.client.phone.confirm()
        self.assertEqual(state.client.phone.confidence, Confidence.UNASKED)
        self.assertIsNone(state.client.phone.value)

    def test_missing_hard_required_shrinks_as_fields_fill_in(self):
        state = IntakeState()
        state.client.first_name.set("Maria")
        state.client.last_name.set("Gomez")
        self.assertEqual(missing_hard_required(state), ["client.phone"])
        state.client.phone.set("555-0100")
        self.assertEqual(missing_hard_required(state), [])

    def test_missing_section_required_reports_per_section(self):
        state = IntakeState()
        missing = missing_section_required(state)
        self.assertIn("intake", missing)
        self.assertIn("client", missing)
        self.assertIn("summary", missing)
        self.assertEqual(
            set(missing["intake"]),
            set(SECTION_REQUIRED_INFORMATION["intake"]),
        )

    def test_add_service_accepts_only_known_types(self):
        state = IntakeState()
        state.services_and_referral.add_service("PCS / CHHA")
        self.assertIn("PCS / CHHA", state.services_and_referral.service_requested)
        with self.assertRaises(ValueError):
            state.services_and_referral.add_service("Physical Therapy")

    def test_add_service_is_idempotent(self):
        state = IntakeState()
        state.services_and_referral.add_service("Companion")
        state.services_and_referral.add_service("Companion")
        self.assertEqual(state.services_and_referral.service_requested, ["Companion"])

    def test_contacts_default_to_the_three_form_types(self):
        state = IntakeState()
        self.assertEqual(
            set(state.contacts.keys()),
            {"information_provider", "emergency_contact", "collateral_contact"},
        )
        for contact_type, contact in state.contacts.items():
            self.assertEqual(contact.contact_type, contact_type)

    def test_to_json_round_trips_through_json_dumps(self):
        state = IntakeState()
        state.client.first_name.set("Maria", confidence=Confidence.CONFIRMED, turn=1)
        state.services_and_referral.add_service("Skilled Nursing (CBSN)")
        payload = state.to_json()
        # Must actually be JSON-serializable (Enums resolved to plain strings).
        dumped = json.dumps(payload)
        reloaded = json.loads(dumped)
        self.assertEqual(reloaded["client"]["first_name"]["value"], "Maria")
        self.assertEqual(reloaded["client"]["first_name"]["confidence"], "confirmed")
        self.assertEqual(
            reloaded["services_and_referral"]["service_requested"],
            ["Skilled Nursing (CBSN)"],
        )

    def test_medical_field_names_are_snake_case_despite_form_labels(self):
        state = IntakeState()
        # Form label is "Medicaid_MLTSS_plan" / "ADL_assistance_needed" --
        # confirm the Python attribute names are the snake_case versions.
        state.medical.medicaid_mltss_plan.set("MLTSS - Amerigroup")
        state.functional_status.adl_assistance_needed.set("bathing, dressing")
        self.assertEqual(state.medical.medicaid_mltss_plan.value, "MLTSS - Amerigroup")


class ServiceTypesTests(unittest.TestCase):
    def test_service_types_match_form_schema_exactly(self):
        raw = json.loads(FORM_SCHEMA_PATH.read_text(encoding="utf-8"))
        form_services = tuple(raw["sections"]["services_and_referral"]["service_requested"])
        self.assertEqual(form_services, SERVICE_TYPES)


class CallConsentTests(unittest.TestCase):
    def test_default_consent_blocks_proceeding(self):
        consent = CallConsent()
        self.assertFalse(consent.may_proceed_past_opening)

    def test_recorded_consent_plus_proceed_outcome_allows_the_call(self):
        consent = CallConsent(had_recorded_consent_to_contact=True)
        consent.opening_outcome = OpeningOutcome.PROCEED
        self.assertTrue(consent.may_proceed_past_opening)

    def test_recorded_consent_but_no_opening_outcome_yet_blocks(self):
        consent = CallConsent(had_recorded_consent_to_contact=True)
        self.assertEqual(consent.opening_outcome, OpeningOutcome.PENDING)
        self.assertFalse(consent.may_proceed_past_opening)

    def test_opt_out_blocks_even_with_recorded_consent(self):
        consent = CallConsent(had_recorded_consent_to_contact=True)
        consent.opening_outcome = OpeningOutcome.PROCEED
        self.assertTrue(consent.may_proceed_past_opening)
        consent.record_opt_out()
        self.assertFalse(consent.may_proceed_past_opening)
        self.assertIsNotNone(consent.opt_out_recorded_at)

    def test_no_recorded_consent_blocks_even_if_someone_marks_proceed(self):
        # Defense in depth: even if opening_outcome is (incorrectly) set to
        # PROCEED, a call that never had recorded consent must not proceed.
        consent = CallConsent(had_recorded_consent_to_contact=False)
        consent.opening_outcome = OpeningOutcome.PROCEED
        self.assertFalse(consent.may_proceed_past_opening)


if __name__ == "__main__":
    unittest.main()
