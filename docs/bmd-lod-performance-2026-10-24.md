# BMD do Paraná: fragmentação, níveis de detalhe e próximos patches

Estado: **investigação documentada; nenhum patch LOD implementado ou instalado**.
Evidência agregada: [evidence-bmd-lod-parana-2026-10-24.json](evidence-bmd-lod-parana-2026-10-24.json).

## Responsabilidade

Esta correção pertence ao `nds-forge`: geração da geometria BMD, política de
classes por escala, metadados SHARED, índices PRODUCT e validação do pacote.
O app consome esses dados; seu pacing de frames e filas de gestos são outra
frente. Aumentar FPS não substitui produzir dados adequados a cada zoom.

Não confundir duas dimensões:

- **FRC/classe de via:** importância da via. As classes já existem no BMD.
- **LOD/nível geométrico por zoom:** conjuntos de tiles com detalhe diferente,
  anunciados por escala. O BMD estadual observado oferece somente um.

O problema não é ter perdido todas as categorias de ruas. É oferecer uma
representação extremamente fragmentada para toda a faixa de zoom. Ainda não
foi demonstrado quanto desse conteúdo o engine processa por quadro.

## Achados comprovados no mapa realmente instalado

O pacote observado é `Geely_EX2_PARANA_v9`, copiado sem alterações. Os hashes
e tamanhos de seus 20 payloads conferiram com o metadata da origem. O mapa
não está neste repositório; os caminhos externos estão no JSON de evidência.

| Identidade | SHA-256 |
|---|---|
| BMD.NDS instalado | `2c18614e676b7c0e3f389093a6884ee8ff46cc50a6d101b33e38a1b7d4289c24` |
| ROUTE.NDS instalado | `f69f4b0533c73ad3f94e163bf1688c9a2d927b904d96428344a2f4f542905887` |
| BMD SQLite decodificado | `5289a265472a5c0f496842a523a6c8e2c7937e2b09cbf700b931eba79a484a87` |

| Métrica | Observado |
|---|---:|
| Tiles BMD | 2.518 |
| Features de vias | 6.260.680 |
| Vértices armazenados | 12.521.361 |
| Features com nome | 1.180.460 |
| Bytes dos blobs | 68.349.303 |
| Features com exatamente 2 vértices | 6.260.679 |
| Features com 3 vértices | 1 |
| Maior tile em features | 51.435 |
| Maior número de vínculos de nomes por tile | 31.258 |

Não são 6 milhões de objetos necessariamente carregados a cada frame. A API
nativa oferece seleção de tiles por área e acesso separado a atributos/pontos.
O total estadual é uma medida do dataset, não um perfil de execução.

O decoder externo leu **todos os 2.518 tiles**, preservou os 15 bits de
atributos, coordenadas NDS, IDs de nomes, count encoding e delta widths, e
recodificou cada blob **byte a byte idêntico**. O formato observado é o
subconjunto gerado de vias/nomes, não um decoder universal de qualquer NDS.
Não há OSM way IDs recuperáveis do BMD; não inventá-los para permitir merges.

### Classes presentes

| FRC | Features |
|---:|---:|
| 0 | 43.735 |
| 1 | 161.036 |
| 2 | 188.794 |
| 3 | 320.750 |
| 4 | 1.977.356 |
| 5 | 3.560.668 |
| 6 | 8.341 |

FRC 4+5 representam aproximadamente **88,46%** dos objetos. O perfil estadual
atual mapeia residential/living_street/busway para 4; service/unclassified
para 5. FRC≤3 retém 714.315 features (11,41%); ≤2 retém 393.565 (6,29%);
≤1 retém 204.771 (3,27%). Isso é um orçamento de seleção, não um ganho de FPS.
Os 12 bits inferiores dos atributos observados são zero, mas o decoder os
preserva e um futuro compilador não deve descartá-los sem validar o contrato.

### Um único nível anunciado

No SHARED do pacote real, a linha de `buildingBlockId=1` é:

```text
levelNumber=11
coordShift=4
minScaleDenominator=1
maxScaleDenominator=50000000
scaleDenominatorList=NULL
```

`bmdScaleLevelTable` está vazia. PRODUCT tem 2.518 entradas BMD de nível 11,
cada uma 1×1 com bitmap `80`. ROUTE tem índice de nível 9 separado; POI tem
suas próprias entradas BB4. Não usar os níveis do ROUTE como detalhe visual.

O SHARED do seed original possui vários níveis BMD (2/3/5/7/9/11/13/14),
shifts e faixas de escala. Níveis 13 e 14 se sobrepõem: copiar as linhas ou
adivinhar precedência não reconstrói o comportamento do consumidor.

## Onde a implementação atual produz esse resultado

Os nomes das funções são a referência principal; as linhas podem mudar.

| Arquivo / função | Contrato atual e mudança necessária |
|---|---|
| `nds_forge/adapters/osm_extract.py` | GeoJSON retém `properties.osm_id`, tags e partes de ways. Preservar essa identidade até o agrupamento BMD; é a fonte correta para merge. |
| `nds_forge/layers/bmd.py::compile_bmd_from_clip` | Lê um `level/shift`. Em `itertools.pairwise(line)`, cada segmento vira uma feature via `grouped[tile].append((points, frc, name))`. Essa estrutura também perde o OSM ID intermediário. Trocar por polilinhas contínuas por way/parte/tile/atributos e uma saída por LOD. |
| `nds_forge/layers/bmd.py::_split_segment_by_tile` | Cortes/quantização por tile já existem. Preservar fronteiras e precisão declarada para cada nível. |
| `nds_forge/layers/bmd_codec.py::encode_road_tile` | Aceita polilinhas, mas o chamador entrega quase sempre pares. Não tem scale-sublevel por feature habilitado. Validar contagens/deltas antes de escrever, sem truncar. |
| `nds_forge/layers/steps.py::_profile`, `_execute_bmd` | Faz merge do grid/road policy no perfil BMD. Precisa transportar política multilevel explícita e incluir essa configuração nos fingerprints. |
| `nds_forge/layers/catalog.py::LayerRule`, `CatalogProfileConfig`, `build_catalogs` | Modelo BMD é singular (`payload_level`, `payload_shift`, lista de tiles). Evoluir para cobertura e referências por nível; não confundir com o índice ROUTE. |
| `nds_forge/layers/package_assemble.py::_write_bmd` | Escreve os blobs do manifest. Precisa aceitar todos os níveis gerados e validar inventário completo. |
| `nds_forge/layers/package_assemble.py::_write_shared` | Apaga BB1/BB2 e insere uma linha de cada com escala 1..50.000.000. O patch BMD deve gerar todas as linhas BB1 coerentes com payloads; preservar a semântica ROUTE BB2. |
| `nds_forge/layers/package_assemble.py::_write_product` | Hoje apaga índices BB1/BB2/BB4 juntos e usa um único nível BMD também para POI. Separar níveis BMD de cobertura POI/ROUTE ao evoluir o catálogo. |
| `examples/neusoft-parana-v9.toml` | `neusoft.grid.bmd.level=11`, `shift=4`; road_policy já possui FRC. O perfil novo precisa declarar níveis/faixas/shifts/filtros, mantendo compatibilidade com o perfil antigo. |

Esta leitura foi feita sobre um checkout com modificações anteriores de outros
trabalhos. Esta entrega acrescenta somente documentação e evidência agregada;
não sobrescreve essas alterações nem declara que elas já incluem LOD.

## Proposta de implementação em etapas

### 1. Reduzir objetos sem mudar detalhe

No caminho OSM→BMD, preservar `osm_id`, parte, nome, FRC e todos os atributos;
unir somente trechos consecutivos com endpoints quantizados iguais, dentro
do mesmo tile e da mesma identidade. Não fazer snapping, nearest-neighbor,
reordenação ou união entre vias diferentes. Não importar scripts do checkout
de investigação no runtime do forge; portar o código revisado para o adapter.

Comparar multiconjunto de segmentos orientados **com atributos** antes/depois.
Regerar associações feature→nome; manter IDs de nomes determinísticos. Não
reconstruir ROUTE/NAME/FTS/POI desnecessariamente num experimento BMD-only.
Caps/joins/posicionamento de labels ainda precisam de comparação visual.

Um candidato municipal externo fez 187.655→43.173 features preservando
segmentos, e o leitor nativo conferiu seus vértices/nomes em Android arm64.
Esse resultado não prova a redução do Paraná nem é validação de um novo build
do forge. A versão estadual precisa ser medida independentemente.

### 2. Gerar detalhe adequado ao zoom

Manter a geometria detalhada como baseline de comparação. Gerar novas
representações a partir do OSM com filtros FRC, quantização e eventual
simplificação **de exibição** documentados por nível. Não aplicar a
simplificação de display ao grafo ROUTE.

Política inicial para avaliar, **ainda não implementada nem validada**:

| Nível | Shift candidato | Escala candidata | FRC máximo candidato |
|---:|---:|---|---:|
| 11 | 4 | 1..50.000 | todas as classes |
| 9 | 11 | 50.001..250.000 | 3 |
| 7 | 13 | 250.001..1.000.000 | 2 |
| 5 | 15 | 1.000.001..5.000.000 | 1 |
| 3 | 17 | 5.000.001..20.000.000 | 0 |
| 2 | 17 | 20.000.001..50.000.000 | 0 |

As faixas/shifts se apoiam na organização do seed original, com escolhas
conservadoras para o experimento; não são uma política nativa inferida.
Confirmar seleção real, transições, ausência de tile e o papel de sublevels.
Não anunciar um nível sem gerar seu payload e catálogo correspondentes.

**Limites já identificados antes da quantização:** L9/FRC≤3 tem maior parent
com 60.808 features; L7/FRC≤2, 57.684. Cabem em uint16. L5/FRC≤1 tem parents
de 132.860 e 71.911 features: não cabem. Nomes por parent também podem exceder
15 bits. A remoção legítima de segmentos colapsados pode reduzir contagens,
mas é obrigatório medir a saída final; nunca truncar ou retirar nomes para
fazer o encoder aceitar o tile. Reavaliar nível, precisão ou agrupamento.

### 3. Atualizar o pacote inteiro de forma coerente

- Manifest BMD versionado com nível, shift, escala, política FRC, contagens,
  hashes e nome de cada payload; source OSM/region/tool/config identificados.
- SHARED BB1 alinhado a todos os níveis gerados; demais building blocks
  preservados ou alterados somente por seu próprio contrato.
- PRODUCT BB1 com a cobertura exata de cada nível. Não reusar cegamente essa
  cobertura para BB4 POI ou BB2 ROUTE.
- IDs de nomes coerentes em todos os níveis e catálogos de busca preservados.
- Metadata JSON com hashes/tamanhos atualizados para cada arquivo alterado.
- Fingerprints/resume invalidados por política LOD, fonte, compilador e codec.

## Evidência nativa e o que ainda falta provar

O leitor de levelMetadata em `libNdsLib.so` observado usa campos level,
building-block, shift e limites de escala. O loader de scaleDenominatorList
consome count u8 seguido de uint32 big-endian; NULL resulta em lista vazia.
Endereços e resumos externos devem permanecer vinculados ao SHA da biblioteca.
Esses fatos não provam sozinhos a seleção/fallback do engine.

Os shaders `packed_line_lit.v.glsl` e `texstyle_line_lit.v.glsl` do APK
consultam estilos por escala; há também ocultação de linhas finas na GPU.
Portanto controle visual por escala já existe. Isso **não** prova que todos
os dados são carregados antes do filtro, nem que não exista pré-filtro CPU.
Ocultar no shader não equivale, por si só, a evitar IO/preparo de geometria.

Questões abertas para o consumidor nativo:

1. Precedência dos níveis sobrepostos e uso de `scaleDenominatorList`.
2. Fallback quando não há tile no nível solicitado.
3. Se o metadata binário de BB1 no PRODUCT contém requisitos adicionais.
4. Em que ponto FRC/escala reduzem carga: seleção, parse, preparação ou shader.
5. Qual nível o engine efetivamente solicita em cada zoom do candidato.

## Validação e comandos para o próximo agente

Tudo continua experimental. Separar: implementação, execução, roundtrip,
aceitação nativa, SDK/UI e performance. Não promover `geometry.bmd` ou
`validation.native` no registry por causa de evidência externa histórica.

```sh
cd /Users/timoteosousa/apps/nds-forge
python3 -m nds_forge plan --config examples/neusoft-parana-v9.toml --target container.pack
python3 -m nds_forge capabilities
python3 -m unittest discover -s tests -p 'test_bmd*.py' -v
python3 -m unittest discover -s tests -p 'test_coverage_catalog.py' -v
```

Esses comandos não geram um candidato estadual validado. `plan` é dry run;
os testes usam fixtures. Não executar download, probes ou dispositivo de
dentro de uma etapa normal. Manter os gates explícitos deste repositório.

Antes de implementar, leia `docs/stages.md`, `docs/adapter-contract.md`,
`docs/validation.md`, `docs/external-artifacts.md` e este documento.

Artefatos de pesquisa externos, relativos ao checkout
`/Users/timoteosousa/apps/geely-ex2-investigation/`:

- `neusoft-maps/performance/parana-bmd-inventory-20261024.json`: contagens
  completas por tile/classe e prova de reencode. Não precisa reconstruir a
  coleta para entender os dados.
- `neusoft-maps/tools/parana_bmd_codec.py`: decoder streaming do formato
  presente e encoder byte-exato. `RoadTile(blob,id,level=11,shift=4).features()`
  produz atributos15bits, pontos NDS inteiros, name_id e parâmetros do encoding;
  não produz OSM IDs. É material de investigação, não dependency do forge.
- `neusoft-maps/performance/parana-bmd-lod-budgets.json`: orçamento por parent
  antes de quantização, sem payload LOD gerado.
- `neusoft-maps/performance/vehicle-r9-20261024T162405Z/map-source/Geely_EX2_PARANA_v9/`:
  oracle exato, fora do repositório; **não** usar como fonte de camadas geradas
  pelo pipeline. O build correto parte dos inputs OSM declarados no perfil.
- `neusoft-maps/performance/vehicle-r9-20261024T162405Z/map-decoded/`:
  BMD/SHARED SQLite para inspeção local autorizada, sem importar os arquivos.

Não adicionar mapas, PBF, APKs, bibliotecas, chaves, credenciais, screenshots
ou logs pessoais a este repositório. O JSON ao lado contém apenas identidades,
contagens e referências externas, sem payload nem coordenadas.

Critérios de aceitação do futuro patch: dados detalhados preservados; limites
numéricos respeitados; nomes íntegros; payload/metadados/cobertura concordam;
native probe e zoom real selecionam o nível esperado; rotas/busca/POIs não
regridem; custo de IO/preparo/renderização melhora no mesmo cenário/temperatura.
Não inferir desempenho a partir da redução de bytes ou da quantidade de tiles.
