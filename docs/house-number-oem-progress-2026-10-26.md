# Busca OEM por número — atualização do carro e parser, 2026-10-26

**Trabalho parado por solicitação explícita do usuário.** Este registro atualiza
o alcance do No-Go do AVD documentado em `house-number-oem-e0-2026-10-26.md`.

No carro, o app chama `nds_rt_getHouseNumbersOfLink` periodicamente com um link
válido. Uma captura de 15 segundos, sem emitir buscas e sem tocar na UI, registrou
cinco chamadas, aproximadamente uma a cada três segundos, pela mesma pilha e
mesma key das chamadas observadas durante os testes de busca. Existe portanto
um consumidor nativo em segundo plano. A classe exata ainda não foi identificada.

Isso reabre E0 para estudar o formato: **Go de existência de consumidor nativo**,
sem comprovar integração da busca digitada. `addressSearch` com número continuou
retornando vazio; a rua sem número retornou um resultado. Não houve consulta
normal às tabelas FTS de número. O No-Go do AVD não deve ser generalizado para
“o app nunca chama APIs de número”.

O teste do carro clonou o mapa ativo e modificou somente ROUTE, em um diretório
`map-test-oem-e0`. O original foi restaurado; rede IPv4/IPv6 e configuração
originais foram restabelecidas. Nenhum APK nem biblioteca foi alterado.

E1 avançou: um parser estrito fora deste repositório leu **3.879/3.879 blobs OEM
(100%)**, incluindo todas as referências, valores e limites exatos dos containers.
Foram decodificados 27.800 descritores e 232.912 grupos. Pontos corrigidos em
relação ao handoff antigo:

- Há um bit opcional antes da MapList (zero em todo o corpus).
- A contagem é `var15`, não u16 fixo; o blob de 24.060 B tem 22 mapas e header de
  167 bytes. O primeiro byte isolado não representa a contagem.
- Contexto de serialização observado: versão 15. A coluna de versão da camada
  vale 1 e o segundo argumento do builder vale 4; são campos distintos.
- Os containers começam com **2 bits** de modo, seguidos de quantidade var15.
  Os modos observados foram 0 e 1, com alinhamentos por byte em posições definidas.
- `0x90`, `0x94`, `0x95`, `0x96`, `0xB6` são **IDs de atributo**. O campo de
  referência de quatro bits observado contém 1, 2, 3, 4 ou 10.
- Valores de nome observados são u32. O atributo `0xB6` possui tipo de quatro
  bits, string, flag e números variáveis. Só tipos 0 e 1 apareceram no corpus.
  Seus significados de paridade/lado/posição ainda não foram validados.

**Ainda não implementados/validados:** varredura E2 por todos os links e direções,
byte patching E3, semântica de número E4, encoder/projeção/roundtrip E5 no Forge.
Não existe correção nova da busca entregue por esta continuação.

Handoff completo e arquivos locais, mantidos fora do Forge por conterem dados
proprietários:

```text
geely-ex2-investigation/neusoft-maps/native/house-number-20261026/oem-investigation/
  HANDOFF-STOP-2026-10-26.md
  e0-car-summary.json
  runtime-e0-car-v1/
  runtime-car-idle/
  parse_oem_layer.py
  parser-e1-v1/report.json
  parser-e1-v1/blobs/       # todos os 3.879 blobs extraídos
  parser-e1-v1/samples/     # modelos decodificados das três amostras
  rea/                    # evidências completas e pseudocódigos
```

O próximo agente deve usar o parser pronto e cruzar seus índices com os getters
nativos no AVD. As chaves corretas são `(UR<<48)|(tile<<16)|(idx<<1)|dir`.
O handoff detalha a assinatura e os offsets preliminares do getter NAME para
obter strings, ainda sem validação em runtime. Nenhum encoder deve ser tratado
como solução da UI enquanto o fluxo de busca não exercitar a resolução de número.

---

# Continuação 2026-10-26 — E2 executado, E3/E4 parciais

**Executado no AVD, sem tocar no carro.** Fixture: cópia do pacote Curitiba v10 com
o blob OEM do tile 540446303 injetado no tile 664743895 e NAME OEM no lugar do NAME
de Curitiba. Handles criados por `nds_open` + `nds_createRoutingDbHandle`.

## E2 — concluído

Varredura `0..2399 × 2 direções` com `(UR<<48)|(tile<<16)|(idx<<1)|dir`:

- nomes: 3.102 hits, 1.551 índices distintos, **maior hit = 2376 = maior índice do
  modelo**; subconjunto exato do modelo (0 hits fora do modelo).
- números: 1.734 hits, 867 índices, maior hit = 2303 = maior índice do modelo.

Conclusão: **o getter indexa pelo espaço de índices das referências do layer** e não
valida contra a contagem de links do tile (o tile tem 1.401 links e responde até 2376).
A filtragem de nomes é por atributo/kind (taxa 65–97% dependendo de 144/148/149/150),
não por índice. `+0x28` dos registros é máscara de direção (3 = ambas).

## E3 — parcial

IDs de atributo e tipos de referência confirmados pelo parser + runtime
(`0xB6`=182 números; `144/148/149/150`=nomes). Falta o patch controlado de atributo
para mapear attr→kind e explicar os 826 índices de nome ausentes.

## E4 — parcial, com ground truth

O bloco de números do layer (`0xB6`, `encoded_type=1`) contém **min/max** de faixa e
bate exatamente com o FTS OEM:

```
link 2 -> layer [2,6] e [1,23]
OEM FTS namedObjectId 67169 -> (type 1, 2..6) e (type 2, 1..23)
```

O getter `nds_rt_getHouseNumbersOfLink` retorna registros de 0x78 bytes
(`+0x06` flags=0x21, `+0x08/+0x0c` números, `+0x10` label "*", `+0x28` máscara,
`+0x2c`=0xffff) e **não retorna `encoded_type=0`** (número único). O link 1 tem 5
faixas no FTS mas nenhuma referência no layer → 0 resultados: o getter lê só o layer.

Patch controlado type 0→1 no índice 3 (re-parse confirma) **não** mudou o resultado
(n=0), indicando uma tabela por link (índice u16 em `lVar7+0x78`, registros de 10 B)
que ainda precisa ser decodificada.

## Pendências

1. E4b: decodificar a tabela de 10 bytes por link usada em `0x1fb658` (gate do type 0).
2. E4c: validar `f06` e label "*" com caso de prefixo/sufixo real.
3. E3: patch de atributo 148→149 e concluir a filtragem de nomes.
4. E5: encoder + roundtrip byte-idêntico; depois projeção e pacote.

Relatório completo, evidências brutas e comandos:
`geely-ex2-investigation/neusoft-maps/native/house-number-20261026/oem-investigation/E2-E4-FINDINGS-2026-10-26.md`

---

## E4b concluído — tabela de pontos e gate (correção importante)

O layer tem **duas famílias de mapa** para o atributo 182 (0xB6):

| reference_type | papel |
|---|---|
| 1 (mapas 18/19 no blob) | números **por link** (ref = link index) — fallback |
| 3 (mapas 20/21) | números **por ponto de endereço** (ref = índice do ponto) |

O getter escolhe por `FUN_001f1020(ctx, link) == 2`:

- `== 2` → caminho por ponto: `FUN_001f1334` devolve índices de registros da tabela
  de pontos; cada registro fornece `ref` em `+2`; valor lido no mapa `+0x28`.
- `!= 2` → fallback: `FUN_001edeec(layer, 4, layer+0x18, ref = linkIndex, ...)`.

Tabela de pontos decodificada (contexto `*(handle+0x120)`, base em `ctx+0x78` por
offset relativo): registros de **10 bytes**, ordenados por link:

```
u16 link | u16 ref (denso, 0..N-1) | u16 pad (0) | u32 bit_offset
```

328 registros no blob injetado; `bit_offset` crescente (0, 109, 276, 399, ...).

**Correção**: `encoded_type=0` **é retornado** — link 1000 (sem ponto) devolve
`[21,21]` e `[22,22]` pelo mapa por link. O link 3 retornava 0 porque só tinha
valores no mapa por ponto e **não possui registro na tabela de pontos**; o patch
type 0→1 no índice 3 não podia funcionar por isso.

Semântica: `type 0` = número único (min=max); `type 1` = faixa min..max (bate com o
FTS OEM). Evidências em
`oem-investigation/e2-e4-20261026/{point-table-records.json,verify-types.json}`.

---

## E4c + E5 concluídos

**E4c (label/flag):** patch controlado no valor do índice 2 provou o mapeamento:
`value.label` → `+0x10` (std::string) e `value.flag` → `+0x06` bit 5 (0x20).
Original `f06=0x21`, label `*`; após patch (`"A"`, flag 0) → `f06=0x01`, label `A`,
números inalterados. O corpus OEM não tem prefixo/sufixo/addressPoint, então a
semântica textual do label segue não observada.

**E5 (encoder):** novo `nds_forge/layers/route_attribute_layer.py` com
`read_attribute_layer`/`write_attribute_layer` (MapList var15, refs 1/2/3/4/10,
containers modo 0/1, valores de nome e de número 0xB6 tipos 0/1/2/3).
Roundtrip **3.879/3.879 blobs byte-idênticos**; testes em
`tests/test_route_attribute_layer.py` (sintético + OEM via `NDS_OEM_LAYER_DB`),
mypy/ruff limpos.

Ainda não integrado ao `route_aux.py` (coluna `nameAttributeLayer`) nem à
projeção ponto→link/pacote.

---

## Integração do codec no pipeline (2026-10-26)

Implementado:

- `nds_forge/layers/route_attribute_compiler.py`
  - projeta cada binding de endereço no link mais próximo (STRtree por tile,
    `tile_for(lon,lat,13)`), limite de 20.000 unidades NDS (~186 m);
  - agrupa por (link, rua) e emite uma faixa `[min,max]` por link (rua dominante);
  - modelo: um mapa `{attribute_ids:[0xB6], reference_type:2, mode:0}`,
    refs = índice do link, máscara 3.
- `nds_forge/layers/route_aux.py`: `compile_route_aux(..., address_dir=None)`
  escreve `<tile>.route-attrs.bin` via `write_attribute_layer` e registra
  `attribute_layer` (path/bytes/sha256/link_ranges) por tile; contagens novas
  `attribute_layer_tiles` e `attribute_link_ranges`.
- `nds_forge/layers/package_assemble.py`: `_write_route` valida o hash do layer e
  grava `routingAuxTileTable.nameAttributeLayer` (coluna + versionId 1).
- `nds_forge/layers/steps.py`: `routing.aux` depende de `address.compile`.
- Testes: `tests/test_route_link_attributes.py` (projeção, faixa, codificação e
  rejeição de endereço distante).

Build Curitiba v10 `--target container.pack`:

```
attribute_layer_tiles 102   attribute_link_ranges 46007
ROUTE.NDS sha256 e51e6f1bbd232072185e474ba3418026ce94381503cef52b843a2cb6eddd9400
FTS.NDS inalterado (67e13dd4…)
```

### Validação nativa (AVD, `nds_rt_getHouseNumbersOfLink`)

Fixture `/data/local/tmp/e3` (pacote novo em plaintext):

```
tile 664743895 link 3  -> n=1 [154,203] mask 3   (modelo: [154,203])
tile 664743895 link 0  -> n=0                    (link sem endereço)
tile 664743897 link 0  -> n=1 [2654,2694]
tile 664743747 link 63 -> n=1 [9585,9585]
```

O layer gerado pelo Forge é aceito pelo builder nativo e consumido pelo getter:
integração código→dados→motor fechada para números (a camada de nomes segue
ausente; `nameCount` = 0 nesses links).

---

## Validação no carro com layer gerado (2026-10-27)

Pacote `Geely_EX2_CURITIBA_v10_attrs` instalado no carro (backup
`install/etc/sdk.ini.before-attrs-20261027`), ROUTE com as 102 camadas.

Engine (chamada direta `nds_rt_getHouseNumbersOfLink`, mesmo handle do mapa):

```
tile 664743895 link 3 -> n=1 [154,203]   (idêntico ao compilado)
tile 664743895 link 0 -> n=0
tile 664743897 link 0 -> n=1 [2654,2694]
```

Consumidor/UI:

- Idle (30 s, sem busca, com GPS válido e app no mapa): **0 chamadas** a
  `nds_rt_getNamesOfLink`/`nds_rt_getHouseNumbersOfLink` — não foi possível
  reproduzir as chamadas periódicas registradas no handoff anterior.
- `addressSearch("Rua Luiz França")` → 1 resultado; `addressSearch("... 71")`
  → 0; **nenhuma chamada** aos getters durante a busca.
- Digitação na UI com "Rua Luiz Franca 71" → lista vazia (offline).

Conclusão: o dado OEM está correto e consumível pelo motor, mas **este firmware
não consulta número na busca digitada**; o caminho OEM sozinho não entrega a
feature na UI. A injeção runtime (opção 1) segue sendo o caminho de produto;
o layer gerado fica como base para o dia em que o consumidor existir/for patchado.
Pacote novo mantido ativo no carro.
