"""Offline source retrieval controls; provider decisions are mocked."""
import importlib.util
from pathlib import Path
import pytest
spec = importlib.util.spec_from_file_location("ask_uniform_queries", Path(__file__).resolve().parent.parent / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

@pytest.mark.parametrize("question", ['how many wheel positions are open on the radar', 'how much detail does EXPLORE.md give on the rollout plan', 'what is the breakeven on nvda right now', 'how much cheaper and faster is this', 'How many put opportunities show up this morning?', 'what does router.md say about routing', 'which section covers the tools audit', 'where is the agentic-economy plan discussed', 'what swap path services did we discover', 'what steps does the restart skill take to restart the seat', 'what is the process for returning a laptop', 'what is the total cost of the trip', 'what is the current balance', 'What car do I have?', 'What time does TP201 depart?', 'What model does the overnight cron job run?', 'how many items are on the radar', 'how much does EXPLORE cover the rollout', 'what is owed on the vehicle account', 'what price did we pay for the Dell after the refund', 'should I renew the lease', 'how do I file the claim'])
def test_query_shapes_share_evidence_criterion(question):
    assert ask.confirm_label(question) == ask.SOURCE_LABEL
