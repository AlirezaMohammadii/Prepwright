"""The HTTP bridge: routing, request limits, static serving.

Owns the socket and the route table. Owns no domain logic: a route validates
its input, calls one function, and shapes the reply.

Status: lives in bridge.py, not yet extracted.
"""
