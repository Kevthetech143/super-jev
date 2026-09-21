# Connector navigation

Purpose: locate candidate files in a reviewed source set before asking whether passages answer a question. Navigation must not be interpreted as answer approval.

Reuse baseline: the existing path connector already handles explicit file review, immutable prepared manifests, principal scope, and freshness. Reuse those boundaries. TypeSafe's [hierarchical classification cookbook](https://docs.typesafe.ai/cookbooks/hierarchical_classification) supplies the traversal pattern: retain a bounded beam of paths and rank using length-normalized Choice probabilities. No Haystack dependency is required for this narrow local traversal; the Haystack retrieval-only pilot did not test this navigation contract or establish an answer-quality advantage.

The connector captures a flat-file or folder-tree view over explicitly supplied files. Its catalog is part of the reviewed manifest. New declared structures/groupings require a preview-bound metadata hash as well as source hashes. Older prepared pointers can expose their existing reviewed sources as a flat view. Folder labels and descriptions are data, not instructions. Original files are unchanged; no directory discovery or external connector protocol is introduced.

The navigate action checks pointer authorization and freshness, sends only the reviewed catalog and original question to the bundled navigation engine, then checks source scope and freshness again before returning candidates. It never writes a cached answer or approves evidence. The caller inspects the returned sources using existing authorized tools.

Limits are explicit: beam width, number of rounds and number of returned sources. Trace information records what was explored. Branch pruning and a limited shortlist cannot establish that no matching file exists. No-candidates and budget exhaustion must retain that limitation. Invalid catalogs/provider IDs and failed transports fail visibly. This is not arbitrary graph/database navigation, automated index parsing, answer synthesis or a demonstrated fix for evidence rejection.

Agent entrypoint and examples: [connector guide](../skills/super-jev/references/connectors.md#navigation-structure). Control panel: `memory --describe`; setup help: `help --topic navigation`.

Live diagnostic: [frozen fixture and results](evidence/connector-navigation-20260920/README.md).
