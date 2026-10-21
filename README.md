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
