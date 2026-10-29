# Estágios do pipeline e critérios de conclusão

Caminho em pesquisa para eliminar conteúdo geográfico herdado:
[schemas novos e primeiras camadas OSM](fresh-nds-without-geographic-bootstrap-2026-10-24.md).
O helper offline ainda não é um assembler NDS integrado ou validado pelo SDK.

Investigação específica de escala estadual:
[BMD do Paraná: fragmentação e LOD](bmd-lod-performance-2026-10-24.md).
São achados e requisitos para `geometry.bmd`, catálogo e assembler; não
alteram os estados de implementação/validação da tabela abaixo.

Protótipo BMD contínuo/multi-LOD e correções offline:
[review do Batel](review-bmd-contiguous-multilod-batel-2026-10-24.md).
Há perfil reproduzível e testes sintéticos; aceitação nativa/SDK e desempenho
do candidato continuam sem validação.

Identidade de produto/região é declarada em `[neusoft.region_binding]` e
validada por `nds_forge.region_binding`; caminhos, catálogo, `PRODUCT.NDS`,
`ROOT.NDS`, `SHARED.NDS`, `Area.txt` e `metadata.json` derivam dela. O caminho
Brasil/Paraná verificado no veículo está em
[`brazil-parana-independent-map.md`](brazil-parana-independent-map.md).

Este documento descreve a fronteira entre o core genérico e os compiladores de formatos Neusoft. A tabela usa os IDs e o `status` atuais do registry, conferidos com `python -m nds_forge capabilities`; status da etapa, execução e validação continuam sendo dimensões diferentes. Uma etapa `planned` não pode ser executada nem contribuir para status de mapa pronto. Uma etapa `experimental` só pode rodar com opt-in explícito e seu resultado não é produto validado.

## Estado do caminho end-to-end

| Ordem | ID no registry | Etapa/capacidade | Estado atual | Entrada e saída observadas/esperadas | Limite e condição para avançar |
|---:|---|---|---|---|---|
| 1 | `region.resolve` | Resolver bbox ou polígono fornecido | `implemented` | TOML → `region.json` | Valida estrutura, coordenadas e CRS WGS84 explícito. Topologia é checada somente com Shapely; relação OSM sozinha não é polígono. Não prova cobertura OSM. |
| 2 | `source.acquire` | Obter PBF | `implemented` | caminho local ou URL declarada → `source.json` + provenance | Calcula hash; URL exige `--allow-network`. Não valida sintaxe PBF nem baixa boundary automaticamente. |
| 3 | `source.verify` | Verificar fonte e referência de região | `implemented` | `source.json` + `region.json` → relatório de proveniência | Valida integridade/referência; parsing e clipping ficam em `osm.clip`, separado e experimental. |
| — | `bootstrap.verify` | Verificar inventário explícito de assets | `implemented` | diretório declarado → `bootstrap-report.json` | Independente do caminho OSM; hashes não validam semântica ou compatibilidade do asset. |
| 4 | `osm.clip` | Recortar e extrair OSM | `experimental` | PBF + limite local → `roads.geojson`, modelo OSM canônico e `clip-report.json` | Exercitado com PBF sintético pequeno, não extrato regional/veículo. Retém `restriction_relations_raw`; a compilação diagnóstica ocorre em `routing.main`, não nesta etapa. Exige opt-in. |
| 5 | `osm.normalize` | Normalizar feature model | `planned` | entidades OSM → modelo canônico versionado | Deve tipar classes, nomes, access/oneway, conectividade, POIs, endereços e restrições, separando extraído de compilado. |
| 6 | `geometry.bmd` | Compilar geometria BMD | `experimental` | `osm.clip/roads.geojson` + perfil → tiles BMD e `street_names.json` | Caminho direto dos dados recortados; não depende de `osm.normalize`. Perfil Curitiba; blobs e saída permanecem sem validação nativa/SDK. |
| 7 | `names.compile` | Preparar registros de nomes e busca | `experimental` | vias recortadas + nomes BMD + perfil locale → `name-records.json` | Gera registros intermediários canônicos; não equivale a aceitação de busca na UI/SDK. |
| 8 | `poi.compile` | Preparar registros POI | `experimental` | fonte/mapeamento OSM ou arquivo curado declarado → `poi-records.json` | Categorias, IDs, cobertura e geometrias têm limites próprios; não declara cobertura geral nem aceitação nativa. |
| 9 | `address.compile` | Compilar endereços e interpolação | `planned` | endereços/relações OSM → tabelas de faixa e índices | Resolver interpolação, lado da via e vínculo a objetos NAME; ainda não integrado ao DAG de pacote. |
| 10 | `routing.main` | Compilar grafo ROUTE principal | `experimental` | PBF + bbox/polígono + perfil Curitiba v9 → modelo, transições diagnósticas e tiles ROUTE | Preserva nós `via` e registra transições direcionais via-node `no_*`/`only_*` no `route-model.json`; não codifica a tabela nativa de transições no ROUTE.NDS e não prova cálculo de rota no SDK. Via-way/conditional e conversões em bordas de tile seguem sem suporte nativo. |
| 11 | `routing.aux` | Compilar geometria ROUTE-AUX | `experimental` | modelo ROUTE + perfil → tiles AUX | Preserva ordinais e protege offset uint16; não codifica turn restrictions, guidance ou aceitação nativa. |
| 12 | — | TMC/traffic location mapping | `planned` (sem etapa registrada) | dataset regional de referência → IDs e tabelas | OSM isolado não fornece TMC/TPEG; matching, licença e cobertura regional continuam pendentes. |
| 13 | `catalogs.build` | Construir índices de cobertura | `experimental` | manifests de camadas + regras por perfil → `catalogs.json` | Planeja cobertura por camada; consistency/semântica do pacote ainda precisam dos validadores independentes. |
| 14 | `package.assemble` | Montar pacote Curitiba v9 | `experimental` | camadas, catálogo e bootstrap → árvore de 21 caminhos | Perfil específico; não herdar IDs implicitamente. Árvore candidata não é validação funcional. |
| 15 | `container.pack` | Aplicar container ZV e publicar paths | `experimental` | pacote SQLite + referências externas de chaves → paths app-readable e manifest | Exige secrets externas; integridade/hash não provam aceitação nativa ou navegação. |
| 16 | `validation.offline` | Validar estrutura/consistência offline | `planned` | pacote → resultado de validação | Existem helpers de manifesto/hash; o passo integrado ao registry/DAG permanece planejado. |
| 17 | `validation.native` | Validar parser nativo | `planned` | artifacts + runtime nativo identificado → report | Opt-in; valida somente operações instrumentadas, não inferir routing/UI. |
| 18 | `validation.device` | Validar SDK/dispositivo | `planned` | candidato + protocolo/aparelho autorizado → callbacks/restauração | Opt-in; separar cálculo de rota, guidance e render. Não executado automaticamente no `run`. |

O snapshot vem de `python -m nds_forge capabilities`; `status` descreve a maturidade registrada, não o estado de uma saída específica. O executor grava `execution_status` e `validation_status` por run. Mesmo quando uma etapa experimental gera seus arquivos, o resultado pode continuar `unvalidated`; nenhuma dessas etapas deve ser descrita como validada só por ter passado nos testes sintéticos. Uma etapa planejada não pode emitir artefato vazio com `completed` para satisfazer uma dependência.

## Fronteiras de adapter

Para limites vetoriais, `is_valid` descreve apenas o resultado topológico:
com Shapely, geometria válida retorna `topology_checked=true`/`is_valid=true`,
enquanto geometria inválida causa erro. Sem Shapely, a validação estrutural e o
cálculo do bbox continuam, mas retornam `topology_checked=false`/`is_valid=null`.
Esses campos são preservados pelo loader, por `ValidatedBoundary.to_dict()` e
em `region.json`; `null` significa não verificado, não aprovação ou reprovação.

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

Uma etapa só declara capacidade que realmente produz. O `osm.clip` atual pode anunciar `road_ways`, `osm_way_ids`, `raw_way_tags` e `restriction_relations_raw`: relações e tags OSM permanecem dados brutos. Ele não anuncia `turn_restrictions`, `addresses`, `poi_catalog` ou `routing_rules`, que exigem compilação semântica posterior.

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
