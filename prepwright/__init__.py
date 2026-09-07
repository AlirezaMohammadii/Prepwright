"""Prepwright: a local, single-user interview-preparation tutor.

Flat by design. Every runtime module sits directly in this package so the
launcher can pin a fixed, enumerable file set with SHA-256. See
docs/adr/0001 for why there is no nesting and no install step.
"""
