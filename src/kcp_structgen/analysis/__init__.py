"""Layer 2: weekly OI + flow + events analysis.

See docs/layer2_spec.md for the architecture. Modules in this package own the
data pipeline; the LLM only sees the structured digest produced by
`aggregator.py`.
"""
