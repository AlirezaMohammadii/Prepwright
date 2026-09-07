"""The request boundary: origin, host, cookie, header, remote identity.

The only module allowed to decide that a request may proceed. Every check is
positive: a request is refused unless it matches something named here.

Status: lives in bridge.py, not yet extracted.
"""
