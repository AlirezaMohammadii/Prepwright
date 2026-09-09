"""Where prompt assembly and the citation check actually live.

This file holds no code and is kept as a pointer, because "prompt" is what a
reader greps for and the concern is split across two modules by design:

- `prepwright/corpus.py` builds the bounded pack for one turn (`build_pack`)
  and verifies the reply against it (`check_citations`). It is below the
  provider layer on purpose, so the research path that fetches a page and the
  teaching path that reads one back share one set of rules.
- `prepwright/teach.py` assembles the turn around that pack: the tutor system
  prompt, the no-errands rule, `evidence_pack` and `chat_via_cli`.

The chain is build_pack -> cites -> check_citations -> citations.invented ->
refusal, and an uncited claim is the failure the whole design exists to
prevent. Do not weaken it to make a walk pass.

Until ADR 0007 this file said "Status: partly in bridge.py". That stopped being
true when the teaching turn moved out of bridge.py, which is now the
composition root and holds no prompt text at all.
"""
