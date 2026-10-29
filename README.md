# NDS Forge

Pipeline local e extensível para transformar uma região escolhida pelo usuário
em artefatos de mapa Neusoft, mantendo procedência, hashes e estado de validação
separado do estado de execução. O core não assume Curitiba, fabricante, região
de catálogo, chaves, templates ou disponibilidade de dispositivo.

**Estado atual:** o registry tem clipping OSM e compiladores de BMD, NAME/FTS,
POI, ROUTE/AUX, catálogos, pacote Curitiba v9 e container ZV marcados como
`experimental`. Eles exigem `--allow-experimental`, profiles explícitos e não
produzem, por si só, um mapa validado pelo parser nativo ou pelo SDK. OSM
normalization, endereços e validação nativa/dispositivo continuam `planned`;
traffic mapping ainda não tem etapa registrada. Veja os IDs e estados reais em
`docs/stages.md`, além de `docs/adapter-contract.md` e `docs/legacy-inventory.md`.

Requer Python 3.11+ e não precisa de dependências externas para `plan`,
validação de config, `status` ou verificação de PBF local.

```sh
python -m nds_forge plan --config examples/curitiba.toml
python -m nds_forge status --config examples/curitiba.toml
python examples/smoke.py
```

O smoke cria bytes de teste temporários para verificar CLI e manifests; eles
**não** são PBF e não testam parsing OSM. Para clipping, instale as dependências
opcionais em ambiente isolado:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[osm]'
```

`plan` é local e não cria estado nem acessa rede. `run` executa etapas
implementadas ou experimentais até o `--target`; qualquer etapa experimental
exige `--allow-experimental`, e clipping também requer as dependências opcionais
`pyosmium` e `shapely`. Baixar URLs exige
`--allow-network` e SHA-256 configurado. Etapas planejadas bloqueiam o DAG antes
de qualquer saída parcial. Por padrão, nenhuma ação nativa ou no dispositivo é
executada.

Configuração TOML é versionada por `schema_version`; exemplos não contêm PBF,
mapas, APKs, chaves ou templates. Saídas são publicadas por diretório de etapa
após staging e verificação de hash. A validação independente deve ser executada
explicitamente e nunca é inferida da geração.

## Região e dados OSM

Região pode ser uma caixa delimitadora ou um arquivo de polígono local em
GeoJSON. Um ID de relação OSM é apenas uma referência até que uma etapa real de
resolução forneça e valide o polígono. Aquisição de PBF local e URL são etapas
separadas do recorte e da normalização. Configurar uma URL não inicia download:
`run` exige `--allow-network`; esta entrega não baixa extratos grandes.

Respeite a atribuição `© OpenStreetMap contributors` e a licença ODbL ao usar,
redistribuir ou produzir bases derivadas. Cada etapa deve manter origem,
licença, instante de captura e checksums.

Clipping experimental de arquivo PBF local, depois de configurar os caminhos do
PBF e do limite no TOML e instalar as dependências opcionais:

```sh
python -m nds_forge run --config examples/curitiba.toml \
  --target osm.clip --allow-experimental
```

O exemplo Curitiba espera arquivos locais que não são versionados. A etapa gera
somente linhas de vias (`roads.geojson`) e um relatório com contagens,
proveniência e capabilities ausentes. Não implementa POIs, endereços,
restrições, grafo de roteamento ou formato Neusoft. A etapa não baixa boundary
nem PBF.

## Identidade independente Brasil / Paraná

`examples/neusoft-brazil-parana.toml` declara explicitamente o namespace local
do mapa Brasil verificado no veículo: grupo `9000`, produto NAV `90000011`,
produto DECO `90000012`, conjunto `9100`, região `9102`/`UR9102`, área `AC`,
versão `N.J6.AC.26.00.02.00` e fuso `America/Sao_Paulo` (UTC-3). Os caminhos
`db/NAV/UR9102/...`, as linhas de catálogo, o fuso em `SHARED.NDS`, o
`Area.txt` e o `metadata.json` são derivados desse bloco; nenhum caminho
`UR439` ou ID israelense é herdado do bootstrap. Os IDs são experimentais e
não oficiais.

```sh
python -m nds_forge plan --config examples/neusoft-brazil-parana.toml
```

O plano valida IDs, versão, fuso, `region_id` de names/POI/route e as seis
chaves de container antes de qualquer escrita. Implementação, camadas e
limites: [identidade Brasil/Paraná](docs/brazil-parana-independent-map.md).

## Guia: gerar um mapa de qualquer estado ou município brasileiro

O caminho abaixo produz um candidato `container.pack` a partir de OSM. Ele não
é um mapa oficial Neusoft e continua `unvalidated` até passar por parser
nativo, SDK e dispositivo. Use sempre dados e limites que você tem direito de
processar e mantenha `© OpenStreetMap contributors` / ODbL 1.0 nos artefatos.

1. **Obtenha o PBF e o limite administrativo.**
   - PBF: extraia o estado ou município de um arquivo OSM (por exemplo,
     Geofabrik) e confira o SHA-256.
   - Limite: prefira o GeoJSON oficial do IBGE (malha estadual ou municipal).
     `bbox` no TOML é aceito, mas inclui área fora do limite administrativo.
   - Instale as dependências: `python3 -m venv .venv && .venv/bin/pip install -e '.[osm,container]'`.
   - Baixe uma vez o bootstrap de schema público (sementes israelenses
     autorizadas) e os inputs de exemplo:
     ```sh
     .venv/bin/python -m nds_forge fetch-inputs \
       --config examples/neusoft-curitiba-v9.toml --allow-network
     ```
     O diretório resultante
     `examples/work/neusoft-curitiba-v9/inputs/israel-original-base-v1.0.0`
     é usado como `neusoft.bootstrap_dir`. Ele fornece schema/assets estáticos,
     não identidade regional.

2. **Crie o TOML a partir de `examples/neusoft-brazil-parana.toml`.**
   Ajuste:
   ```toml
   schema_version = 1
   project_id = "meu-estado-ou-cidade"
   workspace = "./work/meu-mapa"          # relativo ao diretório do TOML

   [region]
   id = "meu-id-local"
   name = "Meu estado/cidade"
   source = "boundary_geojson"            # ou "bbox" com bbox = [o,s,l,n]
   boundary_geojson = "./caminho/limite.geojson"

   [source]
   kind = "pbf"
   path = "./caminho/extrato.osm.pbf"     # relativo ao diretório do TOML
   sha256 = "<sha256 do PBF>"
   license = "ODbL-1.0"
   attribution = "© OpenStreetMap contributors"

   [pipeline]
   target = "container.pack"
   enable_experimental = true

   [neusoft]
   profile_id = "meu-mapa"                # deve igualar region_binding.profile_id
   bootstrap_profile_id = "neusoft-curitiba-v9"
   bootstrap_dir = "./work/neusoft-curitiba-v9/inputs/israel-original-base-v1.0.0"

   [neusoft.region_binding]
   profile_id = "meu-mapa"
   product_group_id = 9000                # namespace local experimental
   nav_product_id = 90000011
   deco_product_id = 90000012
   update_region_set_id = 9100
   update_region_id = 9102                # escolha um ID local livre
   ur_code = "UR9102"                     # deve ser UR<update_region_id>
   area_code = "AC"
   version_name = "N.J6.AC.26.00.02.00"   # N.<xx>.<area>.<yy>.<zz>.<zz>.<zz>
   product_name = "Brasil"
   set_name = "Brasil"
   region_name = "Meu estado/cidade"
   iso_country_code = "BRA"

   [neusoft.region_binding.timezone]
   id = 6
   olson = "America/Sao_Paulo"
   utc_offset_quarters = -12             # UTC-3 em quartos de hora
   name = "Brasilia Time"
   abbrev = "BRT"
   ```
   Mantenha `names.region_id`, `poi.region_id` e `route.region_id` iguais a
   `update_region_id`. As seis entradas `[neusoft.container_keys]` devem apontar
   exatamente para `db/NAV/UR<id>/{BMD,NAME,FTS,POI,ROUTE,SHARED}.NDS`.
   Para nomes por município, use `admin_scope = "state_municipalities"` com
   `municipality_ibge_prefix` do estado; para um recorte estadual simples,
   `admin_scope = "state"` e uma hierarquia país/estado (`class` 0 e 2).

3. **Defina as seis chaves ZV no ambiente.**
   ```sh
   export NDS_KEY_BMD=<32 hex>
   export NDS_KEY_NAME=<32 hex>
   export NDS_KEY_FTS=<32 hex>
   export NDS_KEY_POI=<32 hex>
   export NDS_KEY_ROUTE=<32 hex>
   export NDS_KEY_SHARED=<32 hex>
   ```
   Cada chave deve ter 16 bytes (32 caracteres hexadecimais). Nunca versione
   chaves; o pipeline só as resolve em runtime e não as grava em manifests.

4. **Valide o plano e execute.**
   ```sh
   .venv/bin/python -m nds_forge plan --config caminho/meu-mapa.toml
   .venv/bin/python -m nds_forge run --config caminho/meu-mapa.toml \
     --target container.pack --allow-experimental --progress
   ```
   `plan` não escreve nem acessa rede. O `run` publica cada etapa em
   `workspace/artifacts/` e para no primeiro erro; `--resume`/nova execução
   reaproveita etapas cujo fingerprint não mudou.

5. **Inspecione e implante o candidato.**
   - `artifacts/container.pack/artifact-manifest.json`: caminhos, tamanhos,
     SHA-256, origem e `validation_status`.
   - `artifacts/package.assemble/package-layout.json`: inventário de 21
     caminhos, binding regional e artefatos de origem.
   - `artifacts/container.pack/db/`: árvore pronta para o diretório de mapas
     do app (`path_mapdata` no `sdk.ini`), com `Area.txt`, `ROOT.NDS`,
     `NAV/PRODUCT.NDS` e `NAV/UR<id>/*.NDS`.
   - Faça backup do `sdk.ini`, copie `db/` para um diretório novo do app,
     ajuste `path_mapdata`, reinicie o app/serviço SDK e valide com rota e
     busca reais. A geração não executa nem valida o dispositivo.

Limites conhecidos: o grafo ROUTE usa nível 13/shift 4; restrições via-way,
condicionais, guidance e velocidades legais não são codificadas; POI cobre
apenas as categorias OSM mapeadas no perfil; o recorte por `bbox` pode incluir
estradas fora do limite. Consulte
[`docs/brazil-parana-independent-map.md`](docs/brazil-parana-independent-map.md)
e [`docs/validation.md`](docs/validation.md).

## Perfil explícito de compatibilidade Curitiba v9

`examples/neusoft-curitiba-v9.toml` fixa parâmetros de grid e entradas em
releases versionados com hashes. Seu plano pode ser inspecionado de qualquer
clone sem materializar estado:

```sh
python -m nds_forge plan --config examples/neusoft-curitiba-v9.toml --target container.pack
```

O download dos bundles exige opt-in explícito:

```sh
python -m nds_forge fetch-inputs --config examples/neusoft-curitiba-v9.toml --allow-network
```

O caminho Curitiba v9 registrado contém etapas experimentais e exige opt-in
explícito para executá-las:

```sh
python -m nds_forge run --config examples/neusoft-curitiba-v9.toml \
  --target container.pack --allow-experimental
```

O caminho experimental Curitiba v9 gera artefatos candidatos, ainda sem
validação nativa ou SDK; etapas planejadas continuam bloqueando o DAG antes de
saída parcial. Cada uma das seis DBs ZV geradas requer uma secret de ambiente
distinta, descrita em
[`docs/external-artifacts.md`](docs/external-artifacts.md). As chaves só são
resolvidas em runtime e não entram nos manifests nem nos artefatos publicados.

## Fonte de POIs de Curitiba

O perfil padrão `examples/neusoft-curitiba-v9.toml` seleciona agora POIs OSM,
limitados a 18 categorias nativas comprovadas. O perfil histórico de dez POIs
curados está preservado em `examples/neusoft-curitiba-v9-curated.toml`, com
workspace separado. Consulte [a evidência e os limites de cobertura](docs/poi-native-categories.md).
Trocar a fonte de POIs gera um candidato novo; não herda a validação histórica
de outro pacote.
