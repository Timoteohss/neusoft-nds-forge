# Sessão 2026-10-26 — busca por número de rua (registro completo)

Alvo: `libNdsLib.so` SHA-256
`c34a7e7a6201cb469bd6c9b2d62d36ef606f12a369ce3027969647a34a970e55`.
Documentos irmãos: `HANDOFF-ADDRESS-2026-10-26.md` (handoff técnico),
`OEM-PATH-DESIGN.md` (desenho do caminho nativo), `frida/README.md` (injeção),
`nds-forge/docs/house-number-search-2026-10-26.md` (visão de produto).

---

## 1. Objetivo

Fazer a busca por **número de rua** funcionar no app (`com.neusoft.na.navigation`),
partindo do fix de ruas do outro agente e da constatação de que as tabelas
`ftsHouseNumberRangeTable`/`namedObject2HouseNRTable` estavam vazias.

## 2. Entregas no nds-forge

### 2.1 Compilador OSM → tabelas FTS
`nds_forge/layers/address.py::compile_osm_addresses(dataset_path, street_names_path,
update_region_id)`

- Lê `osm.clip/osm-dataset.json` (nodes **e ways** retidos).
- Resolve `addr:street` (fallback `addr:place`) por nome exato e por nome
  normalizado (`strip_accents` + casefold + colapso de espaços).
- Deduplica por `(namedObjectId, texto do número)`; texto normalizado em maiúsculas.
- Numérico (`^\d+$`): `min = max = n`, tipo EVEN(1)/ODD(2) pela paridade.
  **Sem merge de faixas** (o teste nativo `minHnr <= n <= maxHnr` passaria a casar
  números inexistentes).
- Não numérico (`S/N`, `7A`): tipo POINT(3), `addressPoint` = texto.
- Ranges deduplicadas **globalmente** por `(type, min, max, language, prefix,
  suffix, point)` — espelha o OEM (poucas ranges, muitos bindings).
- Bindings com `named_object_id`, `range_id`, `update_region_id` **e `lat`/`lon`**
  (`_representative_point`: nó → lat/lon; way → centróide).

`nds_forge/layers/address.py::write_house_index(address_doc, street_names_path, out)`

- Índice de runtime: uma linha por rua
  `nome_normalizado<TAB>nome_exibido<TAB>número:lat,lon;número:lat,lon...`.
- Usado pela injeção (ver §5).

### 2.2 Pipeline
- `nds_forge/layers/steps.py`: executor `_execute_address`; etapa
  `address.compile` (EXPERIMENTAL), deps `("osm.clip", "geometry.bmd")`, saídas
  `address-doc.json` + `house-number-index.tsv`.
- `nds_forge/layers/package_steps.py`: `package.assemble` passa a depender de
  `address.compile`.
- `nds_forge/layers/package_assemble.py`:
  - `_write_address(fts_db, address_dir)` → `write_address_tables` + `_quick_check`;
  - chamado após `_write_names`; `address_sha256` no `package-layout.json`;
  - retorno inclui `address_tables`.

### 2.3 Clip de OSM
`nds_forge/adapters/osm_extract.py::clip_pbf`

- Ways **só de endereço** (`addr:housenumber`, sem tag de POI) passam a ser
  retidos; seus vértices **não** são copiados para o dataset (o compilador usa o
  centróide), mantendo o dataset limitado em nós.
- Motivo: endereços mapeados como building way não entravam no pacote.

### 2.4 Testes
- `tests/test_osm_addresses.py` (3 casos): dedupe/normalização/auditoria;
  consulta nativa (`10`, `11`, `S/N`, `7A`, ausências, rua errada); índice de
  runtime (só números com coordenada).
- `tests/test_osm_extract.py` e `tests/test_address_layer.py` seguem verdes.
- `mypy`/`ruff` limpos nos arquivos tocados.

## 3. Números reais (Curitiba v10)

```
address_objects 305601   resolved 300952   ranges 8493
bindings 300952          with_coordinates 300952
numeric_ranges 300798    point_ranges 154
missing_street 23        unknown_street 3384   duplicate_bindings 1242
```
- `address-doc.json` 44,7 MB; `house-number-index.tsv` 8.808.891 B
  (7.997 ruas, 300.963 entradas).
- FTS texto claro 24.567.808 B (`ftsHouseNumberRangeTable` 8.493;
  `namedObject2HouseNRTable`/`houseNR2NamedObjectTable` 300.952).
- Antes do clip novo: 298.216 objetos / 294.172 resolvidos (diff = +7.385
  objetos de building way).

## 4. Build e artefatos

```
cd nds-forge
set -a && . ~/.config/nds-forge/keys.env && set +a
.venv/bin/python -m nds_forge run --config examples/curitiba-v10-full-masks.toml \
  --target container.pack --allow-experimental --progress
```
- `FTS.NDS` sha256 `67e13dd4ae8e2d4ffe9de6ebec6cc56eb9200e0aeb6309b4c6df7af788eb9319`
  (9.415.862 B empacotado).
- `address_sha256 954c5315484ca780792b46f129e1fa6fc74994fe8058c459ed40016ea6d9bdc7`.
- `ROUTE.NDS` inalterado `e91ad791…cbf6`; `NAME.NDS` `64a670d8…144c`.
- `package-final` 92,8 MB.

## 5. Validações

### 5.1 Nativa (sem app)
Chamada direta `nds_fts_getHouseNumberNamedObjIds` no `ROOT.NDS` do pacote novo
(instalado no AVD), com harness Frida:
```
15730 + "71"  -> 1
15730 + "50"  -> 0
16261 + "358" -> 1
16261 + "359" -> 0
```
Confirma existência/ausência ponta a ponta nas tabelas.

### 5.2 API `addressSearch` — injeção runtime (funciona)
`frida/address_inject.js` (processo `com.neusoft.service.sdk`):
- hook `sdk::service::search::CSearchService::addressSearch` (captura o texto;
  std::string libc++ em `params+0x10`);
- hook `nmasdk::thrift::search::ThriftSearchServiceClient::send_onAddressSearchResult`;
- vetor vazio + número existente → injeta `AddressResultInfo` (72 B; nome em
  `+0x08` SSO/long, `GeoPosition` em `+0x20`, poiType em `+0x40`), alocado com
  `_Znwm` para o destrutor do vetor liberar.

Resultado (AVD):
```
"Rua Luiz França 71" -> 1 resultado "Rua Luiz França, 71" @ (-25.4425656,-49.2290365)
"Rua Luiz França 50" -> 0 resultados
"Rua Luiz França"    -> 1 resultado (rua, comportamento original)
```

### 5.3 UI (sugestões) — injeção observada, renderização pendente
`frida/suggestion_inject.js` (processo `com.neusoft.na.navigation`):
- a digitação chama `searchSuggestion` **a cada tecla** (22 chamadas em "teste"),
  **não** `addressSearch`;
- hook `NMASearchController.searchSuggestion` + `NMASearchThirftListenerImpl.onSearchResult`;
  injeta `PoiInfo` (`BaseSearchPoi.poiName`, `itemID = house:<rua>:<n>`,
  `poiPosition`), insere em `PoiSearchResult.pois` e atualiza `searchCount`.
- Log confirma `injected "Rua Luiz França, 71"`; a lista da UI não foi validada
  (o app voltou ao launcher durante o teste).

## 6. Descobertas de engenharia reversa

### 6.1 Serviço de busca é nativo
- Porta 40605 é servida pelo processo `com.neusoft.service.sdk` (mesmo APK,
  `android:process`).
- Servidor Thrift e busca em `libna_thriftserver_sh.so` / `libna_sdk_sh.so`
  (símbolos C++ exportados). Não há JNI/Java para número de porta.
- Entradas: `CSearchService::addressSearch` (`libna_sdk_sh.so`, arquivo `0x12407e4`),
  `searchSuggestion` (`0x123d44c`), `searchKeyword` (`0x123b6b0`);
  `ThriftSearchServiceClient::send_onAddressSearchResult` (`0x5d861c`),
  `send_onSearchResult` (`0x5d5250`).

### 6.2 Layouts usados
- `AddressResultInfo`: 72 B (stride derivado de `(end-begin)>>3` × `0x38e38e39`);
  nome `+0x08` (libc++ string SSO: byte0 = len<<1; long: u64 len<<1|1 em +0, ptr +8),
  `GeoPosition` `+0x20`, poiType `+0x40`.
- `GeoPosition`: ctor `0x4799b0`, `__set_latitude` `0x4799d4`,
  `__set_longitude` `0x4799dc`; setter de posição `0x633af4`.
- `PoiInfo`/`BaseSearchPoi`: `poiName`, `poiPosition`, `itemID`, `resultType`
  (setters `0x6027b4`, `0x6027ac`, `0x6028b8`), `PoiSearchResult.pois` público.
- Chave de `nds_rt_getNamesOfLink` confirmada no decompilado (`0x1fa8c0`):
  `(UR<<48) | (tileId<<16) | (linkIndex<<1) | dir`
  (`param_2>>0x30`, `param_2>>0x10`, `(param_2>>1)&0x7fff`).
  A varredura antiga com `tileId<<32` era inválida.

### 6.3 Frida
- Frida **17 removeu o bridge Java** (`typeof Java === 'undefined'`).
- Para hooks Java: `frida-server` **16.5.9** + cliente `frida==16.5.9`
  (venv separado). Scripts nativos rodam no 17.

## 7. Experimentos negativos (para não repetir)

1. **Só dados não resolve**: o app não chama `nds_fts_getHouseNumberNamedObjIds`
   nem `nds_rt_getHouseNumbersOfLink` durante `addressSearch`/sugestão, em nenhum
   formato testado (vírgula, `nº`, cidade, número antes do nome).
2. **Semântica da sugestão**: o número é descartado do `MATCH` de nome
   (`((rua) AND (luiz) AND (frança*))`) e o resultado volta 0.
3. **Flood de linhas por endereço** no FTS foi descartado como solução:
   qualquer consulta sem número passaria a listar todos os endereços da rua.
4. **Tela de detalhe da rua** não tem campo de número; resultados OEM com número
   no AVD vinham do provedor **online**.

## 8. Oráculo OEM (novo ativo)

- Chave: `neusoft-maps/private/map-key-0.bin` abre o ROUTE OEM
  (`neusoft-maps/live-test-source/Geely_E245_ISR/db/NAV/UR439/ROUTE.NDS`).
- 4.224 linhas aux; **3.879 com `nameAttributeLayer`**; `nameAttributeLayerVersionId = 1`
  em todas; `adas/volatile/truck` NULL.
- Tamanhos: 15 B … 24.060 B, média 1.353,9 B.
- Amostras:
  - 15 B (tiles 540419387/390/472):
    `00 80 80 4a d0 00 00 00 48 00 40 00 00 03 85`
  - 24.060 B (tile 540446303): inicia
    `0b 00 80 4a 08 00 00 05 38 08 04 a0 80 00 01 df 80 80 4a 88 00 00 20 18 …`
- Padrão recorrente `80 80 4a XX 00 00 YY` + descritores `08 04 a0/81/a1/b1` —
  compatível com MapList `{ids, type 4 bits, offset u32}` MSB-first (hipótese).

## 9. Estado dos ambientes

- **AVD** `emulator-5554`: pacote
  `map-test-address/Geely_EX2_CURITIBA_v10_addr2` ativo; frida removido;
  índice em `/data/local/tmp/house-number-index.tsv`; app iniciado.
- **Carro** `10.10.10.116:5555`: **offline** durante a sessão. Pacote ativo
  `_v10_addr` é o build **anterior** (sem coords/ways) — substituir quando voltar;
  backup `sdk.ini.before-addr` já existe.
- Pacotes antigos preservados no AVD/carro: `_v10_fix`, `_v10_searchfix`,
  `_v10_streetsearch`.

## 10. Pendências

1. Validar a renderização/uso da injeção de sugestão na UI (`suggestion_inject.js`).
2. Caminho OEM: F0–F5 do `OEM-PATH-DESIGN.md`; próximo passo decisivo é F1
   (layer OEM + chave correta).
3. Instalar `package-final` novo no carro quando reconectar.
4. Se for para produção sem Frida: patch nativo de `libna_*` ou fechar o encoder
   de atributos.

## 11. Reprodução (resumo)

```sh
# build
cd nds-forge && set -a && . ~/.config/nds-forge/keys.env && set +a
.venv/bin/python -m nds_forge run --config examples/curitiba-v10-full-masks.toml \
  --target container.pack --allow-experimental --progress

# índice + injeção
adb -s <serial> push examples/work/curitiba-v10/artifacts/address/house-number-index.tsv \
  /data/local/tmp/house-number-index.tsv
adb -s <serial> shell chmod 644 /data/local/tmp/house-number-index.tsv
# frida-server 16.5.9 (Java bridge p/ sugestão) ou 17 (address)
adb -s <serial> forward tcp:27042 tcp:27042
# ver frida/README.md para o script de attach

# oráculo OEM
cd geely-ex2-investigation
python3 neusoft-maps/tools/unpack_nds.py \
  neusoft-maps/live-test-source/Geely_E245_ISR/db/NAV/UR439/ROUTE.NDS \
  /tmp/oem-route.nds --key-file neusoft-maps/private/map-key-0.bin
```
