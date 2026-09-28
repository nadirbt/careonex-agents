"""Call-level consent state -- kept separate from intake/state.py.

See intake/README.md, "Two different artifacts -- don't conflate them," for
why. In short: consent to be contacted is captured on the public
careonex.com web form, upstream of any call. This module models the two
things that ARE decided at call time:

1. Did the scheduler verify recorded consent exists before dialing
   (SOW stage 0, "Eligibility to call" -- a rule, not a model)?
2. Did the family reconfirm "yes, now is a good time" at the top of THIS
   call, or say "no" / "stop calling" (SOW stage 1, "Opening")?

Success metrics this directly supports (SOW Section 3):
  "Calls to numbers without recorded consent" target: 0
  "opt-outs honored" target: 100%
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class OpeningOutcome(str, Enum):
    PENDING = "pending"  # opening script not yet run
    PROCEED = "proceed"  # family said now is a good time
    RESCHEDULE = "reschedule"  # family asked to be called back later
    OPT_OUT = "opt_out"  # family said "no" / "stop calling"


@dataclass
class CallConsent:
    """One instance per call attempt."""

    # Set by the scheduler (SOW stage 0) BEFORE dialing. This is a
    # rule-based gate the agent cannot override -- if this is False, the
    # call must not be placed at all. Modeled here so the same object can
    # be asserted on in tests and logged for the "0 calls without consent"
    # metric, not because the agent decides this.
    had_recorded_consent_to_contact: bool = False
    consent_source_form_submission_id: Optional[str] = None

    # Set during the Opening stage (SOW stage 1) of THIS call.
    opening_outcome: OpeningOutcome = OpeningOutcome.PENDING
    opt_out_recorded_at: Optional[datetime] = None
    recording_disclosed: bool = False

    def record_opt_out(self, *, at: Optional[datetime] = None) -> None:
        self.opening_outcome = OpeningOutcome.OPT_OUT
        self.opt_out_recorded_at = at or datetime.now(timezone.utc)

    @property
    def may_proceed_past_opening(self) -> bool:
        """Gate the agent must check before moving past the Opening stage.

        False if consent to contact was never verified (should have blocked
        the dial entirely -- this is a defense in depth check) or if the
        family opted out or asked to reschedule during the Opening.
        """
        if not self.had_recorded_consent_to_contact:
            return False
        return self.opening_outcome == OpeningOutcome.PROCEED
