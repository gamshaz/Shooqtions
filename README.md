# KCP STIR Structure Generator

Desktop tool for the KCP rates sales desk. Type a trade scenario in natural language; get a grouped list of listed STIR option structures formatted in PricingMonkey trade-description grammar. Copy, paste into PM, PM prices and validates.

Products v1: SOFR, Euribor, SONIA — whites and reds mid-curves. Listed options + futures only.

See [CLAUDE.md](CLAUDE.md) for architecture, [docs/spec.md](docs/spec.md) for the frozen v1 spec, [docs/plan.md](docs/plan.md) for the build order.

## Install (dev)

```
pip install -e .[dev]
```

Requires Python 3.11+ and a working `claude` CLI (Claude Code under your Enterprise seat).

## Run

```
python -m kcp_structgen
```

## Test

From the project root, after `pip install -e .[dev]`:

```
pytest
```

- Enumerator, strike, and product tests run without Claude Code.
- Golden parser tests (`tests/golden/*.json`) auto-skip if the `claude` CLI isn't on PATH. On a desk PC with Claude Code installed they run automatically.
- To run just one file: `pytest tests/test_enumerator.py`
- To see verbose output: `pytest -v`

