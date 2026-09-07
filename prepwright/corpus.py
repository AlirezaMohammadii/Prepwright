"""The distilled document store, its index, and citation lookup.

Implements DESIGN-state-corpus.md sections E and G. Reads are contained by realpath
under exactly one track's subtree and refuse symlinks outright, which is what
makes cross-track isolation a mechanism rather than a promise.

Status: corpus_path/corpus_evidence live in bridge.py; index and per-track roots not built.
"""
