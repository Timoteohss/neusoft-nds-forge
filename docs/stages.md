# Estágios do pipeline e critérios de conclusão

Este documento descreve a fronteira entre o core genérico e os compiladores de formatos Neusoft. O plano é transformar OSM em camadas de navegação, preservando a cadeia de proveniência. Uma etapa `planned` não pode ser executada nem contribuir para status de mapa pronto. Uma etapa `experimental` só pode rodar com opt-in explícito e seu resultado não é produto validado.

## Estado do caminho end-to-end

| Ordem | Etapa/capacidade | Estado atual | Entrada e saída esperadas | Condição para avançar |
|---:|---|---|---|---|
| 1 | Resolver região | Implemented no core inicial | TOML → bbox ou referência a polígono local, com `region.json` | Faz validação estrutural/tipo e calcula bbox de coordenadas GeoJSON; não valida topologia, CRS ou cobertura OSM. Relação OSM sozinha não é polígono. |
| 2 | Obter PBF | Implemented para PBF local; URL requer opt-in de rede | caminho local ou URL declarada → `source.json` + provenance | Hash calculado; URL somente com `--allow-network`; origem e licença declaradas são metadados do usuário. Não valida sintaxe PBF nem baixa boundary automaticamente. |
| 3 | Resolver polígono de região | Parcial no core: lê GeoJSON fornecido; resolver/fetch não existe | GeoJSON local ou fonte explicitamente resolvida → polígono + origem/hash | O core verifica estrutura e calcula extent; validação geométrica robusta e CRS estão pendentes. Resolução de fronteira remota é atividade separada com permissão de rede. |
| 4 | Recortar e extrair OSM | Experimental; adapter `nds_forge.adapters.osm_extract` exercitado localmente, registro no DAG em integração | PBF local + polígono/bbox → vias recortadas em `roads.geojson` + `clip-report.json` | Testado com PBF sintético pequeno: bbox, polígono, corte de borda, IDs/tags/oneway, área sem vias e rejeição de região não resolvida. Não testado com extrato regional, parser Neusoft nem dispositivo. Só exporta ways `highway`; relações, nodes como entidades, POIs, endereços, restrições e semantics de routing ficam ausentes. |
| 5 | Normalizar feature model | Planned | entidades OSM → modelo canônico versionado | Guardar source IDs/tags, tipar classes, nomes/aliases, access/oneway, conectividade, POIs, house numbers, relações de endereço e restrições. Relatório de cobertura separa extraído de compilado. |
| 6 | Compilar cartografia BMD | Planned no core; codec e builder Curitiba são experimentais legados | ruas normalizadas + parâmetros de tile/schema → BMD tiles + names refs + report | Parâmetros declarados (level, shift, FRC/policy); validar blobs/schema, limites e índices. Parser/renderer nativo são validações separadas. |
| 7 | Compilar nomes e busca | Planned | objetos nomeados/admin + aliases → NAME/FTS e índices | IDs locais sem colisão, associação geométrica e escopo/região consistentes. Search tests offline e consulta SDK têm estados distintos. |
| 8 | Compilar POI | Planned | POIs normalizados → POI DB + FTS + tiles/catalog links | Categorias/mapeamentos configurados, IDs consistentes, cobertura e duplicatas auditadas. Dez POIs curados de Curitiba não contam como cobertura geral. |
| 9 | Compilar endereços | Planned | endereços e relações OSM validados → house-number range tables e indexação | Resolver interpolações, lado da via e vínculo a objetos de nome; não há builder legado e ausência é explícita. |
| 10 | Compilar ROUTE main | Experimental na pesquisa legada; planned no core | vias topológicas, tags de acesso/sentido → interseções/links por tile | Grafo topológico, refs inter-tile, atributos, níveis/índices consistentes; parser nativo isolado não basta para prova de cálculo. |
| 11 | Compilar AUX/forma e regras de navegação | Experimental parcial (geometry AUX); restrictions/guidance planned | links + formas + regras → AUX, turn restrictions, acessos condicionais e guidance | Guardar limite de offset de geometria: início relativo da row é uint16 em bits (≤65.535); não confundir com tamanho total do blob. Restrições, flags e guidance precisam de modelo e prova nativa própria. |
| 12 | TMC/traffic location mapping | Planned | vias/ligações e dataset regional TMC/TPEG ou outro location reference → IDs e tabelas | Dados/fornecedor/licença/cobertura declarados; matching e validação regional. TMC legado herdado de ISR não é cobertura Curitiba nem feed de tráfego. OSM isolado não contém esse mapeamento. |
| 13 | Empacotar catálogo e regiões | Planned | camadas validadas + binding de schema/catalog explicitamente fornecida → produto lógico | Não herdar silenciosamente URI/regionId/product IDs de template. Conferir níveis, coverage tile indexes, region/country, metadados, relações entre DBs. |
| 14 | Container/crypto | Planned como adapter opcional | DB SQLite comum + key access externo → container compatível | Formato/versão suportados, hashes, round-trip/integridade. Chave fica fora do repo e não aparece em logs. Container válido não prova compatibilidade funcional. |
| 15 | Validação offline | Implemented como infraestrutura de manifesto/hash no core; validadores por layer planned | artifacts/manifests → resultado por etapa | Validar schemas, integridade, contagens, referências cruzadas e limites; registrar suite e versão de validator. Não promover status automaticamente. |
| 16 | Validação parser nativo | Planned/opt-in | fixtures/artifacts + ambiente nativo autorizado → report | Requer binário/runtime identificado por hash. Valida somente o que foi instrumentado (ex. decode de tile), nunca inferir routing/UI. |
| 17 | Validação SDK no dispositivo | Planned/opt-in explícito | pacote candidato + protocolo/aparelho → callback SDK e restauração verificável | Bloqueada por default. Pré-requisitos de readiness e controle precisam passar; separar cálculo de rota, guidance e render. Nunca instalar nem mudar app config durante `run` normal. |

Status de implementação, execução e validação são dimensões distintas. O core inicial mantém `implementation_status` na definição da etapa e `execution_status`/`validation_status` no manifesto. Exemplo futuro: `routing.main` pode concluir a geração, passar no parser nativo e ainda ser experimental porque não houve cálculo SDK. Uma etapa planejada não pode emitir artefato vazio com `completed` para satisfazer uma dependência.

## Fronteiras de adapter

O core controla DAG, config, validação, staging, logs sanitizados, manifest e publicação atômica dos artefatos. Cada compilador externo é um adapter com configuração tipada e capabilities declaradas. O core não importa `tools/` legado, código Java/NDS nativo, nem secrets.

Contrato de etapa recomendado:

```toml
[steps.osm_clip]
adapter = "osm.clip.features"
status = "planned" # planned | implemented | experimental | blocked
depends_on = ["source.pbf", "region.geometry"]
inputs = ["artifacts/source/pbf", "artifacts/region/polygon.geojson"]
outputs = ["artifacts/osm/roads.geojson", "artifacts/osm/manifest.json"]
capabilities = ["road_ways", "osm_way_ids", "raw_way_tags"]
```

O exemplo é uma especificação de configuração; não habilita uma etapa planejada. Quando implementado, um adapter deve receber diretórios de entrada/saída explicitamente, validar schema e criar o manifesto junto da saída. Para comando externo, passar vetor `argv` sem shell interpolation. Um adapter que invoca legado declara limites de região e marca maturidade experimental.

Cada resultado de adapter deve informar pelo menos:

- `step`, `adapter`, `adapter_version`, `status` de execução e `maturity`;
- `inputs` e `outputs` identificados por nomes lógicos, SHA-256, bytes e schema;
- `provenance`: fonte/URL se conhecida, captured-at, OSM attribution/license e identificadores da fronteira;
- `counts`: entidades vistas, aceitas, rejeitadas e ausentes por motivo;
- `capabilities_produced` e `capabilities_missing`, sem confundir preservação de tags com compilação semântica;
- `validation`: validator/version, evidências e limitações;
- `warnings` e `errors` sem segredos, conteúdo de chaves ou tokens.

Uma etapa só declara capacidade que realmente produz. Por exemplo, o extrator atual pode anunciar `road_ways`, `osm_way_ids`, `raw_way_tags`; não pode anunciar `addresses`, `turn_restrictions`, `poi_catalog` ou `routing_rules` porque esses objetos não aparecem na saída `roads.geojson`.

## Módulos do novo projeto

- `source/`: adapters locais/URL para PBF e provenance. Sem fetch silencioso.
- `region/`: leitura/validação de bbox/polígono e provenance de fronteira.
- `osm/`: clipping e modelo normalizado independente de Neusoft.
- `layers/bmd`, `layers/search`, `layers/poi`, `layers/address`, `layers/route`: compiladores separados que consomem o modelo canônico e possuem suas próprias validações.
- `catalog/`: schemas/bindings externos de produto, regiões, níveis e índices; nenhum ID de template implícito.
- `container/`: codec opcional, dependente de secret provider externo.
- `validation/offline`, `validation/native`, `validation/device`: níveis diferentes, com os dois últimos opt-in e nunca chamados automaticamente.

## Critério geral para chamar um mapa de pronto

Um mapa não é `ready` só porque a cadeia DAG executou. O manifest de release exige capability matrix por camada, proveniência/licença, checksums, consistência cross-layer, resultado offline e evidências do nível de validação afirmado. Se ROUTE, endereços, POI completo, traffic matching ou guidance não estiverem implementados/validados, registrar a lacuna e limitar a descrição de produto à cobertura demonstrada.
