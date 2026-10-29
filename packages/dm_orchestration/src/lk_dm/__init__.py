"""Lanternkeeper DM orchestration primitives.

This package holds provider adapters, typed proposal schemas, context/prompt
assembly, and output safety checks. It never touches the database: the API
service loads state, calls these helpers, and commits through validated tools.
"""
