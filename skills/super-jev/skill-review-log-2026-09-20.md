# Skill review — Super Jev front door

Utility wrapper; same-agent review using review-skill guidance.
Proof of read: "The agent interprets natural language; this dispatcher only executes the chosen tool."

Q1: Compose existing tools. Added four direct dispatch choices; retained the original checking CLI and moved its long instructions into a reference. No new inference, thresholds or search algorithm.
Q2: Added explicit Claude/Codex root selection, dependency errors, and preservation of arguments, backend output and exit status. Explained that legacy keyword ask is not the agent-facing routing step.
Q3: No success claim from a command merely existing. Synthetic subprocess tests cover dispatch boundaries; installed live search/retrieval receipts are saved locally. Record first misses and partial evidence in the configured non-riding card before rescue; private details stay local. Missing write access is pending, never claimed saved.

One review round. User authorized implementation; no additional skill approval gate.
