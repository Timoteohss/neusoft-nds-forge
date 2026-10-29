# Busca por número de rua — estrutura implementada (2026-10-26)

**Estado mais recente:** [carro e parser OEM](house-number-oem-progress-2026-10-26.md).
Há consumidor nativo periódico no carro e parser completo para os 3.879 blobs;
a integração da busca digitada segue aberta. Trabalho parado a pedido do usuário.

Resultado anterior de viabilidade: [E0 no AVD](house-number-oem-e0-2026-10-26.md)
resultou em **No-Go no AVD para a adição isolada de camada ROUTE**. O blob OEM
foi aceito e legível pelas APIs diretas, mas os fluxos normais não consultaram
números. Parser e encoder ficaram suspensos pelo critério de parada solicitado.

Registro completo da sessão: `house-number-session-2026-10-26.md`.
Complementa `street-search-native-fix-2026-10-26.md` (identidade FTS↔NAME e classe).
Alvo: `libNdsLib.so` SHA-256
`c34a7e7a6201cb469bd6c9b2d62d36ef606f12a369ce3027969647a34a970e55`.

## Fluxo nativo (contrato)

Esta é a cadeia desejada com as APIs nativas; a UI atual não a executa
automaticamente. Ver a evidência de E0 acima.

1. Usuário digita `Rua X 50`.
2. FTS resolve a rua: `docId & 0xffffffff` = `namedObjectId` (classe 11).
3. Com o `namedObjectId`, o SDK consulta número:
   `nds_fts_getHouseNumberNamedObjIds` → SQL verbatim em
   `nds_forge/layers/address.py::query_house_number_native`:
   - casa `ftsHouseNumberRangeTable` por `minHnr <= n <= maxHnr` e
     `type = paridade(n)` ou `type = 0`, ou `type = 3 AND addressPoint = texto`;
   - restringe a `rangeId IN (SELECT namedObject2HouseNumberRangeId & 0xffffffff
     FROM namedObject2HouseNRTable WHERE packed >= (namedObjectId<<32)
     AND packed < ((namedObjectId+1)<<32))`.
4. `houseNR2NamedObjectTable` é o índice reverso (`rangeId<<32 | namedObjectId`).

## Estrutura nova no Forge

- `nds_forge/layers/address.py::compile_osm_addresses(dataset_path, street_names_path, update_region_id)`
  - lê `osm.clip/osm-dataset.json` (nodes e ways retidos com `addr:*`);
  - resolve `addr:street` (fallback `addr:place`) contra `geometry-bmd/street_names.json`
    por nome exato e por nome normalizado (sem acentos, casefold);
  - deduplica por `(namedObjectId, texto do número)`;
  - numérico (`^\d+$`): uma range por número, `min=max=n`, tipo par/ímpar
    (nunca mescla faixas — o teste nativo passaria a casar números inexistentes);
  - não numérico (`S/N`, `7A`): tipo 3 (POINT) com `addressPoint` = texto em maiúsculas;
  - ranges deduplicadas globalmente por `(type, min, max, language, prefix, suffix, point)`,
    espelhando o OEM (poucas ranges compartilhadas por muitas ruas);
  - bindings por rua (`namedObjectId`, `rangeId`, `updateRegionId`);
  - auditoria: `address_objects`, `resolved`, `missing_street`, `unknown_street`,
    `duplicate_bindings`, `numeric_ranges`, `point_ranges`, `ranges`, `bindings`.
- Etapa `address.compile` (EXPERIMENTAL), dependências `osm.clip`, `geometry.bmd`,
  saída `address-doc.json`; executor em `nds_forge/layers/steps.py::_execute_address`.
- `package.assemble` agora depende de `address.compile`; `assemble_v9` chama
  `_write_address` (em `package_assemble.py`) depois de `_write_names`, que usa
  `write_address_tables` (DELETE + INSERT das três tabelas) e registra o hash
  `address_sha256` no `package-layout.json` e como input do FTS.

## Números reais (Curitiba v10, 2026-10-26)

```
address_objects 298216 resolved 294172 missing_street 13 unknown_street 3267
duplicate_bindings 764 numeric_ranges 294108 point_ranges 64
ranges 8332 bindings 294172
```

Comparação de forma com o OEM (Curitiba v9 Israel): 6.088 ranges / 108.530 bindings.
Ordem de grandeza compatível; ranges globais confirmam o desenho “poucas ranges,
muitos bindings”.

## Verificação

- `tests/test_osm_addresses.py` (2 testes): compilação com dedupe/normalização e
  consulta nativa (10, 11, `S/N`, `7A`, ausências e rua errada).
- `tests/test_address_layer.py` continua passando (packing, schema, SQL nativo).
- 57 testes relevantes (pipeline contract, name/poi writer, lineage, schema,
  street search, addresses) passam.
- mypy/ruff limpos nos módulos tocados.

## Validação — o que foi observado (26/10)

- Sonda offline no carro (`searchSuggestion`, modo 0):
  `Rua Luiz França` → 1 resultado; `Rua Luiz França 71`, `Rua Luiz França, 71`,
  `Rua Mateus Leme 358` → 0.
- Captura de SQL: a consulta com número vira
  `nameFtsTable MATCH '(((Rua))) AND (((Luiz))) AND (((França))) AND (((71*))))'`,
  ou seja, a API de sugestão trata o número como termo de nome e **nunca** consulta
  as tabelas de número. Nenhuma SQL de `ftsHouseNumberRangeTable` é emitida.
- A tela de detalhe da rua no app não tem campo de número.
- Logo: o teste nativo de números precisa chamar
  `nds_fts_getHouseNumberNamedObjIds` diretamente (cadeia de despacho documentada em
  `geely-ex2-investigation/neusoft-maps/native/street-search-20261026/HANDOFF-ADDRESS-2026-10-26.md`),
  ou encontrar o fluxo de UI que a invoque.

## Estado final (26/10, segunda rodada)

### Resolvido no lado de dados
- **Building ways com endereço**: `osm.clip` agora retém ways que só têm
  `addr:housenumber` (sem tag de POI), sem copiar os vértices para o dataset
  (o compilador usa o centróide). Curitiba: 305.601 objetos de endereço
  (+7.385), 300.952 resolvidos, 8.493 ranges, 153 pontos não numéricos.
- **Coordenadas preservadas**: cada binding carrega `lat`/`lon`
  (`_representative_point`: nó → lat/lon; way → centróide dos vértices),
  300.952/300.952 com coordenada. `address-doc.json` = 44,7 MB.
- **Tabelas nativas**: `ftsHouseNumberRangeTable` 8.493,
  `namedObject2HouseNRTable` 300.952, `houseNR2NamedObjectTable` 300.952
  (FTS.NDS = 24,6 MB).
- **Validação nativa direta** (pacote novo instalado, chamada
  `nds_fts_getHouseNumberNamedObjIds` no `ROOT.NDS`):
  `15730+71 → 1`, `15730+50 → 0`, `16261+358 → 1`, `16261+359 → 0`.
  Confirma número existente/inexistente ponta a ponta nas tabelas.
- **`updateRegionId`/`languageCode`**: não precisam de ajuste. O SQL nativo não
  filtra `updateRegionId` nas tabelas de binding e `languageCode NULL` casa pelo
  ramo `IS NULL`.
- **`addr:place`** continua aceito como fallback de rua. `associatedStreet` não
  foi implementado: em Curitiba apenas 23 objetos ficam sem `addr:street`
  (3.384 com rua desconhecida, fora do NAME), então o ganho seria marginal.

### Bloqueio real: o app não consulta números
Evidência coletada em `addressSearch` (Thrift, modo OFFLINE) e na sugestão:
- Nenhuma chamada a `nds_fts_getHouseNumberNamedObjIds` (hook no export
  `0x9337c`) nem a `nds_rt_getHouseNumbersOfLink` (`0x93a04`) durante
  `Rua Luiz França 71`.
- Nenhuma SQL de número (`ftsHouseNumberRangeTable` ausente do log), com ou sem
  vírgula, com cidade, com `nº`, número antes do nome, etc.
- O número é descartado da consulta de nome
  (`MATCH '(((rua)) AND (((luiz))) AND (((frança*))))'`) e o resultado volta 0;
  a consulta de rua sem número volta 1 com coordenada.
- O fluxo local de endereço é o mesmo da busca por nome; não há parser de número
  nem resolução de faixa no firmware atual.

Ou seja: as tabelas e a consulta nativa estão corretas e validadas; falta o
**consumidor** chamar a consulta. Isso não se resolve por dados.

### Caminhos para integrar (fora do escopo só-dados)
1. **Patch no serviço do SDK** (`com.neusoft.service.sdk`): no handler de
   `addressSearch`, detectar número final na consulta, resolver a rua por nome,
   chamar `nds_fts_getHouseNumberNamedObjIds(handle, 0, item, 0, 20, &id, &out)`
   e montar `AddressResultInfo` com o ponto do binding. Exige fonte de
   coordenada em runtime (as tabelas FTS não têm coluna de coordenada) —
   hoje só existe no `address-doc.json` gerado pelo Forge.
2. **ROUTE aux com atributos de número de porta** (refTypes `0xB6/0xBA` no
   coletor `FUN_001ec76c`, lidos por `nds_rt_getHouseNumbersOfLink`): é o
   caminho nativo "de fábrica" para número + interpolação no link, mas depende
   do encoder de camada flexível que ainda não fecha (mesma classe do layer de
   nomes, ver `status-2026-10-26.md`).
3. **NAME com geometria**: gerar objetos NAME com geometria para o ponto — não
   serve para números (não são named objects) e não muda o parser de endereço.

### Artefatos
- Pacote: `examples/work/curitiba-v10/artifacts/package-final` (92,8 MB).
- `FTS.NDS` sha256 `67e13dd4ae8e2d4ffe9de6ebec6cc56eb9200e0aeb6309b4c6df7af788eb9319`
  (9.415.862 B empacotado; 24,5 MB texto claro). `address_sha256`
  `954c5315484ca780792b46f129e1fa6fc74994fe8058c459ed40016ea6d9bdc7`.
  `ROUTE.NDS` segue `e91ad791…cbf6`.
- Instalado no AVD: `map-test-address/Geely_EX2_CURITIBA_v10_addr2`.
  Carro estava offline no momento; o pacote `_v10_addr` no carro é o build
  anterior (sem coords/ways) e deve ser substituído quando voltar.


## Opção 1 — implementada em runtime (26/10)

O serviço de busca é nativo (`libna_sdk_sh.so` + `libna_thriftserver_sh.so`); não há
JNI nem método Java para número de porta. A integração foi feita por hook:

- `geely-ex2-investigation/neusoft-maps/native/house-number-20261026/frida/address_inject.js`
  - hook `CSearchService::addressSearch` (captura do texto) +
    `ThriftSearchServiceClient::send_onAddressSearchResult`;
  - com vetor vazio e texto terminando em número existente, injeta um
    `AddressResultInfo` (72 B: nome em `+0x08`, GeoPosition em `+0x20`, poiType em `+0x40`).
  - **validado**: `"Rua Luiz França 71"` → `"Rua Luiz França, 71"` na coordenada do
    endereço; `"… 50"` → 0; `"Rua Luiz França"` → resultado normal.
- `.../frida/suggestion_inject.js` (UI digita via `searchSuggestion`): injeta `PoiInfo`
  em `PoiSearchResult.pois`. Injeção confirmada no log; **renderização na UI ainda não
  validada**.

Índice consumido: `address.compile` agora também grava `house-number-index.tsv`
(1 linha por rua; `nome_normalizado<TAB>nome<TAB>número:lat,lon;...`), 8,8 MB para
Curitiba (7.997 ruas, 300.963 entradas).
