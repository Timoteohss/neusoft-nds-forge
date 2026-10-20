# Public contract fixtures

`neusoft_curitiba_v9_package.json` is an output inventory contract. It lists
relative package paths, storage headers, and intended origin mechanism for the
`neusoft-curitiba-v9` profile. It is not a map, package, bootstrap asset, or
baseline input. The profile must generate or derive its layers from declared
OSM/profile inputs; a completed v9 package is an external comparison oracle
only and must never be used as a build input.

The fixture deliberately contains no proprietary NDS files, PBF, decoded keys,
SDK, APK, vehicle data, or private logs. Baseline assets are BYO and must be
verified from their bootstrap manifest and hashes outside this repository.

Origin roles are based on builder mechanism: generated layers are NAV BMD,
NAME, FTS, POI, and ROUTE; derived outputs are NAV PRODUCT, NAV SHARED,
ROOT.NDS, and metadata; the remaining twelve allowlisted paths are copied
external assets. The observed candidate has fifteen `ZV-zlib` containers,
three plain SQLite indexes, one text file, and one JSON metadata file. The
header checker verifies magic/serialization shape only; it does not decrypt or
validate NDS contents. A matching path/hash is not, by itself, evidence of
origin.
