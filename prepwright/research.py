"""Source search, source vetting, and distillation into the corpus.

The ONLY module in this package permitted network access. It runs rarely and
deliberately. Everything downstream reads what it stored, never the network,
which is what keeps a teaching turn cheap and grounded.

Status: not built.
"""
