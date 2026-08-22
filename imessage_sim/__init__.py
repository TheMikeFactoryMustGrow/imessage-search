"""Hermetic iMessage world: fake chat.db, no Apple, no network, no send."""
from imessage_sim.kinds import KINDS
from imessage_sim.run import evaluate_kind, run_all

__all__ = ["KINDS", "evaluate_kind", "run_all"]
