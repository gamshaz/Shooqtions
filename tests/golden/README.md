# Golden tests — scenario → expected params

Each `.json` file in this directory is one hand-labelled scenario. The test runner (`tests/test_parser_golden.py`) feeds `scenario` through `parse_scenario()` and asserts the result matches `expected` exactly.

## File format

```json
{
  "scenario": "fade hawkish fomc sfrz6 tight around 97, flies and condors",
  "expected": {
    "product": "SR3",
    "expiry": "Z6",
    "anchor_price": 97.00,
    "directional_view": "bullish_price",
    "families": ["fly", "condor"],
    "tightness": "tight",
    "cost_preference": null,
    "broken_direction_flag": null,
    "raw_scenario": "fade hawkish fomc sfrz6 tight around 97, flies and condors"
  }
}
```

## Adding new scenarios

1. Write a scenario the way a desk member would actually type it.
2. Fill in `expected` by hand.
3. Save as `NN_short_name.json` (e.g. `07_ecb_dovish_march.json`).
4. Run `pytest tests/test_parser_golden.py`.

Authoring is the user's job. The test runner gates prompt changes — any edit to `parse_scenario.md` that breaks a golden test needs sign-off.

## When to edit vs. add

- **Edit** if an existing scenario's expected output changed because the schema changed.
- **Add** if you want to cover a new phrasing or a new edge case.
- **Delete** only if the scenario is genuinely obsolete (e.g. a product we no longer support).
