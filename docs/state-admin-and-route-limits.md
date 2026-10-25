# State-scale administrative fallback and route restriction limits

The Paraná example is an experimental bounding-box configuration, not a validated
full-state map release. Its bbox is not the state polygon and may include nearby
territories. No state package was rebuilt or installed during this correction.

## Administrative names

`neusoft.names.admin_scope = "state"` declares an intentionally limited fallback:
only country (class 0) and state (class 2) administrative names are generated.
Municipality classes 9/10 and fixed municipality/postcode criteria C/D/J are
rejected. Street names and search aliases remain searchable without associating
every road with Curitiba or a Curitiba postcode. This guard runs at name
compilation and package assembly, before bootstrap copying or secret resolution.
The SQL writer also rejects stale municipal records supplied to a state profile.

The fallback does **not** assign a municipality to each street, distinguish equal
street names in different cities, supply house numbers, or establish that the
Neusoft UI accepts a country/state-only hierarchy. These require OSM municipal
boundary joins, an appropriate per-street/name model, native SDK checks and a car
test. The default municipality profile keeps its four-record v9 contract.

## Route restrictions

`routing.main` now reads OSM restriction relations and adds a directional
transition diagnostic to `route-model.json`. It also protects via nodes from
degree-two merging. The core `layers/route_rules.py` compiler remains an
in-memory rule compiler: the generated diagnostic is **not** written into
Neusoft `ROUTE.NDS`, and native navigation does not yet enforce it.

With the default `fail_closed_unknown=True`:

- Known supported via-node `no_*` and `only_*` rules constrain link transitions.
- Unknown and conditional via-node rules conservatively block every outgoing
  transition from the affected incoming way; this covers conditional `only_*`
  rules without accidentally allowing the forbidden alternatives.
- Unsupported via-way or missing-via restrictions raise
  `UnsupportedRestrictionError`; no permissive transition model is returned.
- Node ID zero is treated as a valid via node.

The graph builder uses `fail_closed_unknown=False` only to record supported
transitions and attach audits for unsupported rules; this mode is for inspection
and its output must not be described as proving legal routing. Via-way,
conditional, tile-boundary and complete OSM restriction semantics remain outside
the native subset.

Regression tests use small synthetic graphs and temporary SQLite FTS tables:

```sh
python3 -m unittest tests.test_route_rules tests.test_name_package_writer -v
```

They verify alternate/no-route behavior, state-scope rejection through the actual
registered package executor, and street search without invented city/postcode
values. They do not build Paraná or access the car.
