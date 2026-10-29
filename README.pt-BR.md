# NDS Forge — guia rápido (pt-BR)

O README principal, em português, tem a referência completa e o TOML comentado:
[README.md](README.md#guia-gerar-um-mapa-de-qualquer-estado-ou-município-brasileiro).
Este arquivo resume o caminho para gerar um candidato de mapa de qualquer
estado ou município brasileiro.

O resultado é **experimental e `unvalidated`**: geração não prova aceitação por
parser nativo, SDK, UI ou veículo. Dados OSM exigem atribuição
`© OpenStreetMap contributors` e ODbL 1.0.

1. **Prepare entradas e dependências.**
   - PBF local do estado/município e SHA-256.
   - Limite administrativo GeoJSON do IBGE (estadual ou municipal); `bbox` é
     aceito, mas pode incluir área fora do limite.
   - ```sh
     python3 -m venv .venv
     .venv/bin/pip install -e '.[osm,container]'
     .venv/bin/python -m nds_forge fetch-inputs \
       --config examples/neusoft-curitiba-v9.toml --allow-network
     ```
     O bootstrap público extraído em
     `examples/work/neusoft-curitiba-v9/inputs/israel-original-base-v1.0.0`
     fornece schema/assets estáticos, não identidade regional.

2. **Copie e ajuste `examples/neusoft-brazil-parana.toml`.**
   Defina `project_id`, `workspace`, `[region]` (`boundary_geojson` ou `bbox`),
   `[source]` (PBF, SHA-256, licença, atribuição) e
   `[neusoft.region_binding]`: IDs locais, `ur_code = "UR<update_region_id>"`,
   `area_code = "AC"`, `version_name = "N.J6.AC.26.00.02.00"`, país `BRA` e
   fuso `America/Sao_Paulo` com `utc_offset_quarters = -12` (UTC-3).
   `neusoft.profile_id` deve igualar `region_binding.profile_id`;
   `names.region_id`, `poi.region_id` e `route.region_id` devem igualar
   `update_region_id`. As seis `[neusoft.container_keys]` devem cobrir
   `db/NAV/UR<id>/{BMD,NAME,FTS,POI,ROUTE,SHARED}.NDS`.
   `bootstrap_profile_id = "neusoft-curitiba-v9"` e `bootstrap_dir` apontam
   para o bootstrap do passo 1.

3. **Exporte as seis chaves ZV.**
   ```sh
   export NDS_KEY_BMD=<32 hex>
   export NDS_KEY_NAME=<32 hex>
   export NDS_KEY_FTS=<32 hex>
   export NDS_KEY_POI=<32 hex>
   export NDS_KEY_ROUTE=<32 hex>
   export NDS_KEY_SHARED=<32 hex>
   ```
   Cada valor representa 16 bytes. Não versione chaves.

4. **Planeje e gere.**
   ```sh
   .venv/bin/python -m nds_forge plan --config caminho/meu-mapa.toml
   .venv/bin/python -m nds_forge run --config caminho/meu-mapa.toml \
     --target container.pack --allow-experimental --progress
   ```

5. **Verifique e implante.**
   Confira `artifacts/container.pack/artifact-manifest.json` (hashes/tamanhos) e
   `artifacts/package.assemble/package-layout.json` (21 caminhos e binding).
   A árvore `artifacts/container.pack/db/` é o `path_mapdata` do `sdk.ini`.
   Faça backup do `sdk.ini`, copie para um diretório novo, ajuste o caminho,
   reinicie app/serviço SDK e valide rota e busca no dispositivo.

Limites: ROUTE nível 13/shift 4; restrições via-way/condicionais, guidance e
velocidades legais não são codificadas; cobertura POI limitada ao perfil;
`bbox` pode incluir vias fora do limite. Veja
[`docs/brazil-parana-independent-map.md`](docs/brazil-parana-independent-map.md)
e [`docs/validation.md`](docs/validation.md).
