"""The one place where the reviewer would act on a result.

In shadow mode it never does: the list of actions for every record is empty, and a test proves it. The function exists
so that giving one outcome type authority later is a small, separately reviewed change that does not reshape the
pipeline (docs/SPEC.md, "Ready to switch on").

The stated aim is that every outcome type is handled automatically in the end. None is today, and the settings cannot
make one so: "shadow" is the only authority this code accepts.
"""
from typing import List

from .config import AUTHORITY, MODE, Config, ModeRefused

# Two outcome types are never to be decided by the panel alone, however well it has matched the maintainer: a change
# to the blueprint, and ruling out a dead end, which adds to the Idea Graveyard. A wrong decision on either does the most harm.
# Passing the graduation count lets the panel pass such a proposal up to the third layer of review, and nothing more
# (docs/SPEC.md, section 9). Nothing here gives any authority today.
PASSED_UP_NOT_DECIDED = ("changed_blueprint", "dead_end")


def most_it_could_ever_be_given(outcome_key: str) -> str:
    """The most this outcome type could be handed once it has passed its count: "pass_up" or "decide"."""
    return "pass_up" if outcome_key in PASSED_UP_NOT_DECIDED else "decide"


def authority_for(config: Config, outcome_key: str) -> str:
    """What the reviewer may do with a result of this type. Today the only answer is "shadow": record it, do nothing."""
    configured = dict(config.authority_outcomes).get(outcome_key, AUTHORITY)
    if configured != AUTHORITY:
        raise ModeRefused(f"authority.outcomes.{outcome_key}: only \"shadow\" is accepted")
    return AUTHORITY


def actions_for(record: dict, config: Config) -> List[dict]:
    """The actions to apply for one record. Always an empty list."""
    if config.mode != MODE:
        raise ModeRefused("mode: only \"shadow\" is accepted")
    return []
