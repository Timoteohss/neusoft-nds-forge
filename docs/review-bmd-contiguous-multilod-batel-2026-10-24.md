# Review: polilinhas contínuas e multi-LOD no Batel

Branch: `feat/bmd-contiguous-multilod-batel`.
Commit revisado: `6ae2fc0c13fd6375eb373c3535ade1dc5f5e63e1`.
Base: `main`; seis arquivos alterados no diff da branch.
Referência: [investigação do Paraná](bmd-lod-performance-2026-10-24.md).

**Atualização após o review:** R1–R5 corrigidos no checkout desta branch, com
testes de regressão. As seções de achados abaixo descrevem o commit original;
o resultado das correções está na seção final. Ainda sem validação nativa/SDK.

## Parecer

A direção corresponde aos achados: reduzir a fragmentação por segmento,
preservar FRC/nome, gerar payloads por nível e anunciar esses níveis no
SHARED/PRODUCT. A implementação é um protótipo útil em uma área pequena,
mas ainda precisa das correções abaixo antes de virar candidato de pacote.
Não há demonstração de seleção de LOD no SDK nem de ganho de performance.

O commit declara 523 entradas no Batel, 523 features L11 e 184 features
L9/FRC≤3. Isso representa 35,18% das features de L11 no nível menos detalhado,
mas não mede redução causada pela união de segmentos: é necessário comparar
o compilador anterior e o novo sobre a mesma entrada. A política local de
classes difere da distribuição estadual; não extrapolar essa proporção.

O PBF e os manifests dessa execução não estão presentes neste checkout.
Esses números foram lidos da mensagem do commit, não reproduzidos neste review.
Não adicionar o PBF ao repositório; registrar evidência agregada e hashes.

## Achados que exigem correção

### R1 — P1: cruzamento exatamente num vértice perde a divisão por tile

Local: `nds_forge/layers/bmd.py::_split_polyline_by_tile`, linhas 73–85 e 105–109.

`cuts` contém apenas os extremos da polilinha e cruzamentos internos de
segmentos. Se um vértice original cair exatamente numa fronteira, não entra
como corte. Um intervalo pode abranger dois tiles e recebe um único tile,
escolhido pelo ponto médio.

Reprodução executada, L11/shift4:

```python
from nds_forge.layers.bmd import _split_polyline_by_tile
edge = -49.306640625
line = [[edge - .01, -25.44], [edge, -25.44], [edge + .01, -25.44]]
print([tile for tile, points in _split_polyline_by_tile(line, 11, 4)])
# Obtido: [142209789]
# Splitter da main por segmento: [142209788, 142209789]
```

A parte oeste passa a ser anunciada somente no tile leste, quebrando cobertura
e podendo desaparecer quando apenas o tile correto é solicitado. Incluir
todos os índices inteiros de vértices como limites dos intervalos de trabalho;
depois unir intervalos consecutivos do mesmo tile. Testar os dois sentidos,
fronteiras horizontais, vértices de retorno e multiconjunto de segmentos com
FRC/nome, não apenas contagens de vértices.

### R2 — P1: índice de POIs depende da ordem dos níveis BMD

Local: `nds_forge/layers/package_assemble.py::_write_product`, linhas 419–427.

O BB4 é reconstruído usando `levels[0]`, apesar de o catálogo ter cobertura
própria em `layer_coverage.poi`. Uma configuração válida `[L9, L11]` produz
POIs anunciados em L9 mesmo quando seus registros permanecem em L11.

Reprodução executada com catálogo sintético:

```text
BMD levels: [L9 tile 34053935, L11 tile 142209789]
POI catalog: L11 tile 142209789
PRODUCT BB4 obtido: L9 tile 34053935
```

Gerar BB4 pelo contrato da camada POI e sua cobertura, independentemente da
ordem de BMD. Se o formato exigir cobertura ampliada, declarar e testar essa
regra na camada POI. Testar permutação de níveis, POIs vazios e POIs presentes.
O acoplamento BMD→POI já existia para o caso singular; a branch introduz a
dependência da ordem e ainda não resolve a independência requerida pelo LOD.

### R3 — P2: catálogo SQLite grava nível incorreto para tiles coarse

Local: `nds_forge/layers/catalog.py`, agregação em 417 e publicação em
`compile_sqlite_catalog`, linhas 248–254.

O JSON agrega IDs de todos os níveis em `tile_ids`, mas o escritor SQLite
continua usando um único `cov.level`. No cenário L11+L9 reproduzido:

```text
layer_coverage BMD no SQLite:
  (level=11, tile_id=34053935)   # deveria ser nível 9
  (level=11, tile_id=142209789)
```

`PRAGMA integrity_check` passa porque isso é inconsistência semântica.
Iterar `levels[]` no escritor SQLite e conservar nível/shift corretos em cada
referência. Comparar cobertura JSON↔SQLite↔PRODUCT. O teste novo desativa
`generate_sqlite_index`, portanto não exercita a saída normal dessa função.

### R4 — P2: níveis duplicados sobrescrevem payloads já referenciados

Local: `nds_forge/layers/bmd.py::_compile_levels`, linhas 287–301 e 315–331.

Não há validação de unicidade de `level`, e o diretório usa apenas esse campo.
Dois registros L11 com shifts 4 e 5 são aceitos. O segundo sobrescreve o blob;
o manifest conserva a primeira referência com hash antigo.

Reprodução executada com a fixture de `tests/test_bmd_multilevel.py`:

```text
bmd_levels = [L11 shift4, L11 shift5]
compilação: aceita
referência com SHA divergente: levels/11/tiles/142209789.bmd.bin
```

Rejeitar duplicatas antes de escrever qualquer payload. Se futuramente forem
permitidas variantes no mesmo nível, elas precisam de identidade e contrato
de seleção próprios; o modelo atual não oferece isso.

### R5 — P2: escalas ausentes viram NULL, em vez de política válida ou erro

Local: `nds_forge/layers/bmd.py::_compile_levels`, linhas 290–301;
`catalog.py` e `package_assemble.py` propagam os valores.

`bmd_levels=[{"level":11,"shift":4}]` é aceito e gera
`min_scale=None, max_scale=None`. O `.get(key, default)` posterior não aplica
default porque as chaves já existem com `None`. O SHARED recebe NULL ou a
montagem falha se o schema seed proibir NULL. Não há política válida de
seleção por escala definida por esse resultado.

Exigir inteiros positivos e `min_scale <= max_scale`; tratar ausência
explicitamente antes da publicação. Definir a política de gaps/sobreposições
para o experimento, sem alegar ter reconstruído a precedência nativa.

## Lacunas da prova em escala menor

- `examples/batel-v9-bmd-repro.toml` declara apenas L11/shift4. Não contém
  `bmd_levels`, portanto seu uso normal não reproduz a execução L11+L9
  declarada no commit. Acrescentar um perfil multi-LOD separado ou o override
  exato utilizado, com hashes dos manifests e resumo de métricas.
- A chave de agrupamento preserva `osm_id`, FRC e nome, mas perde a identidade
  da parte (`feature.id`/índice de MultiLineString). Partes distintas que
  terminem no mesmo ponto quantizado podem ser unidas. Manter o limite de
  parte; com `osm_id` ausente, não permitir união entre features distintas.
- O teste chamado `roundtrip_preserves_vertices_and_names` não compara
  igualdade de geometria antes/depois nem decodifica os vínculos de nomes;
  reencoda e verifica apenas saída não vazia e tabela de nomes presente.
  Adicionar comparação independente de segmentos orientados e associações.
- Limites estaduais continuam relevantes: 65.535 features por tile,
  contagem de vértices e deltas, e 32.767 vínculos de nomes. O codec herdado
  não rejeita explicitamente overflow dos 15 bits da contagem de nomes.
  Não atribuir esse defeito à branch, mas bloquear a expansão estadual sem
  validá-lo. Falhar com erro explicativo; nunca truncar nomes/segmentos.
- Os payloads L11 deixam de ser byte-idênticos por desenho; a preservação
  precisa ser semântica. O codec pode subdividir segmentos longos para
  respeitar deltas de 16 bits: a comparação deve reconhecer subdivisão
  legítima sem aceitar mudança de traçado.
- Emissão e escrita SQLite não validam o leitor nativo. Faltam probe do
  pacote gerado, seleção real por zoom, labels/caps/joins e medição de
  IO/preparo/render no mesmo cenário. Nenhum teste de carro foi executado.

## Verificações executadas neste review

```sh
python3 -m unittest discover -s tests -p 'test_bmd*.py' -v
.venv/bin/python -m unittest discover -s tests
git diff --check main...HEAD
```

Resultado: 16 testes BMD passaram; 246 testes da suíte passaram no `.venv`;
diff sem erros de whitespace. O Python global não tem Shapely e a tentativa
da suíte completa nele teve dois erros por dependência ausente; o ambiente
do projeto resolveu essa limitação. Houve ResourceWarnings de conexões SQLite
na suíte, sem falha final.

As reproduções R1–R5 usaram dados sintéticos e diretórios temporários;
nenhum código de produção, mapa, pacote instalado ou dispositivo foi alterado.
Este review acrescenta apenas este documento.

## Ordem sugerida para continuidade

Corrigir R1/R2; corrigir propagação e validação R3–R5; acrescentar testes de
regressão dos casos reproduzidos; publicar o perfil Batel multi-LOD reproduzível
e métricas antes/depois da mesma entrada. Só depois testar aceitação nativa e
transições de zoom. O Batel é um bom teste inicial; não cobre os limites de
densidade e contagens do Paraná.

## Correções implementadas após autorização

- **R1:** cada vértice original delimita um intervalo de classificação; apenas
  intervalos consecutivos do mesmo tile permanecem na mesma polilinha.
  Testados cruzamentos horizontais/verticais, inversão de sentido e retorno.
- **R2:** PRODUCT BB4 e `poiVirtualTileTable` consomem a cobertura POI do
  catálogo. A verificação de cobertura de POIs usa apenas o nível correspondente
  à sua própria regra, sem misturar IDs de níveis coarse. A procedência do
  escritor POI agora inclui `catalogs.build`.
- **R3:** o índice SQLite percorre `levels[]` e grava o nível de cada tile;
  sua chave inclui camada, nível e tile. O teste de integração de catálogo
  passou a verificar a saída SQLite real contra o JSON gerado.
- **R4/R5:** `nds_forge/layers/bmd_levels.py` centraliza a validação usada no
  compilador, catálogo e assembler. Rejeita níveis duplicados, ausência de
  escalas, tipos não inteiros, limites fora de uint32 e faixas invertidas ou
  sobrepostas. Gaps são permitidos e significam ausência de cobertura
  declarada; não afirmamos haver fallback nativo.
- **Partes:** removida a união posterior por endpoints. Cada parte é
  processada separadamente; nem partes distintas da mesma way, nem features
  sem OSM ID, nem visitas não consecutivas a um tile são unidas.
- **Nomes:** o encoder rejeita mais de 32.767 vínculos antes de codificar a
  geometria. Não trunca vínculos. Um novo teste compara o multiconjunto de
  segmentos orientados, atributos e IDs de nomes decodificados com a entrada.

Novo perfil: [batel-v9-bmd-multilod-repro.toml](../examples/batel-v9-bmd-multilod-repro.toml).
Mantém o exemplo singular e usa workspace separado. Configura L11/shift4 em
1..50.000 e L9/shift11/FRC≤3 em 50.001..250.000, com a mesma fonte e hash.
O teste carrega esse TOML e usa o mesmo `_profile` do executor registrado,
compilando uma fixture sintética em ambos os níveis.

```sh
# Plano local, sem rede ou alteração de dispositivo:
.venv/bin/python -m nds_forge plan \
  --config examples/batel-v9-bmd-multilod-repro.toml --target geometry.bmd

# Execução futura, depois de disponibilizar o PBF local com o hash declarado:
.venv/bin/python -m nds_forge run \
  --config examples/batel-v9-bmd-multilod-repro.toml \
  --target geometry.bmd --allow-experimental

# Regressões e verificações executadas após as correções:
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check nds_forge tools tests
.venv/bin/mypy nds_forge
```

Resultado final: **255 testes passaram**, Ruff e Mypy passaram; plano do
perfil multi-LOD aceito. Os testes de regressão dos achados foram executados
antes das correções e reproduziram as falhas; depois passaram. Permanecem
ResourceWarnings de conexões SQLite na suíte, sem falha final.

O PBF real do Batel não está disponível neste checkout. Não executamos o
build real, não atualizamos os números 523/184 nem medimos ganho de desempenho.
Não houve probe nativo, instalação no carro, commit ou push nesta correção.
