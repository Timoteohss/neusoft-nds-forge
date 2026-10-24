# Cajuru: pacote local de teste multi-LOD v10

Gerado em 2026-10-24 na branch `feat/bmd-contiguous-multilod-batel`, incluindo
as correções locais do [review](review-bmd-contiguous-multilod-batel-2026-10-24.md).
Estado: build completo e validação offline; **SDK/carro não testados**.

Atualização: a instalação posterior autorizada e a inicialização no SDK foram
concluídas; veja a seção final. O ZIP e seus relatórios de build são imutáveis
e conservam o estado de validação registrado antes da instalação.

Perfil: [cajuru-contiguous-multilod-v10.toml](../examples/cajuru-contiguous-multilod-v10.toml).
Workspace local ignorado pelo Git: `examples/work/cajuru-v10/`.
O pacote, mapas, PBF, schemas e chaves não são adicionados ao repositório.

## Entrega local

- ZIP: `examples/work/cajuru-v10/delivery/Geely_EX2_CAJURU_multilod_v10.zip`.
- Tamanho: 80.023.562 bytes.
- SHA-256: `f7157f0655e8f67b3273ad7d0ec78e07d2eefa5a434eafe42da1c60f5de7d5e6`.
- Dentro do diretório homônimo: `db/` com os 21 caminhos do perfil v9,
  `README.md`, `pois.csv`, `candidate-validation.json`, `pairwise-comparison.json`,
  `artifact-manifest.json`, `build-profile.toml` e `SHA256SUMS`.
- O conteúdo de mapa para a central é `db/`; os demais arquivos são suporte.
  Nenhuma instalação ou alteração de APK/dispositivo foi feita nesta entrega.

## Fonte e recorte

Vias/POIs: PBF local do Paraná com SHA-256
`cc2f1f5aa4b46378969fce2274a14bb4c3f2e2f8bbf63f280086fc231580d885`,
© OpenStreetMap contributors, ODbL 1.0. Não houve download de outro PBF.

Recorte: bairro Cajuru, código 21, da
[camada Bairro do IPPUC/GeoCuritiba](https://geocuritiba.ippuc.org.br/server/rest/services/GeoCuritiba/Publico_GeoCuritiba_MapaCadastral/MapServer/2),
consultada com `UPPER(nome) = 'CAJURU'`, geometria em EPSG:4326.
GeoJSON bruto com SHA-256
`4891bf811dc7c217bee6fabcbf9e7ba09d8af2c8b767462568c3b5dc0cf3c8aa`.
Fonte informada pela camada: Decreto Municipal 774/1975. Resposta preservada
somente nos inputs locais; não se afirma licença de redistribuição do limite.

## Conteúdo e comparação

| Nível | Shift | Escalas | Tiles | Features | Vértices |
|---|---:|---|---:|---:|---:|
| L11 | 4 | 1..50.000 | 2 | 1.494 | 7.286 |
| L9, FRC≤3 | 11 | 50.001..250.000 | 2 | 312 | 1.463 |

547 nomes únicos. POIs: **41** em 12 categorias habilitadas, incluindo
farmácias, postos, restaurantes e bancos. Os nomes e coordenadas OSM estão
em `pois.csv`; exemplos: Farmácias Nissei, Droga Raia, Ipiranga, Posto Max,
Farmatotal Centenário e Churrascaria Marumbi. Não são POIs inventados para teste.

Comparação L11/shift4 sobre o mesmo `roads.geojson`, SHA-256
`fb9b3be8b4660303b9f0cb8796c0e21b5ef245d03477ddc101a82a6f6a4fbefc`:
compilador pairwise da main `3fcca511ef1c7fcce18d78b0b1c868cb3d7cd1f2`
produz 5.792 features; candidato produz 1.494, **redução de 74,21%**.
O multiconjunto dos 5.792 segmentos orientados, incluindo tile, atributos
e nome resolvido, é exatamente igual. Isso não é medição de FPS.

## Verificações executadas

- Os 14 passos até `container.pack` completaram; sem rede durante o pipeline.
- Hash/tamanho de todos os 21 arquivos e 20 referências do metadata conferidos.
- Os seis containers ZV foram decodificados novamente para SQLite idêntico,
  por hash, aos bancos produzidos pelo assembler; integridade SQLite conferida.
- Todos os quatro tiles BMD decodificados e recodificados byte a byte iguais;
  payloads conferidos contra BMD.NDS e referências de nomes contra NAME.NDS.
- Níveis/shifts/escalas SHARED e cobertura PRODUCT BMD/POI conferidos.
- Tiles virtuais POI concordam com o catálogo e 41 registros chegaram ao POI.NDS.
- ZIP verificado com `testzip()`; SHA-256 e inventário de arquivos publicados.

Relatórios e scripts locais: `examples/work/cajuru-v10/validation/`, inclusive
`validation/tools/`. O decoder independente é referenciado do checkout de
investigação; não foi incorporado como dependência do forge. Secrets foram
resolvidas em memória a partir dos arquivos externos existentes e não entram
no perfil, ZIP, logs ou manifests.

Limites: apenas o bairro está coberto; não se promete navegação fora dele.
Seleção/fallback de LOD, labels, renderização e performance precisam do SDK.
ROUTE continua experimental, sem turn restrictions/guidance completos; assets
auxiliares herdados do bootstrap não representam tráfego brasileiro.
Nenhuma capability foi promovida por causa desta geração offline.

## Por que o pacote ocupa 80 MB

Unidades desta seção: MB decimais (1 MB = 1.000.000 bytes).
O PBF de entrada tem 167.050.754 bytes e cobre o Paraná inteiro, com entidades
e tags OSM. O recorte Cajuru e a compilação não preservam todas essas entidades:
produzem camadas NDS específicas para exibição, nomes, POIs e roteamento.
Portanto PBF estadual e pacote local não medem a mesma área nem conteúdo/formato.

O inventário exato dos 21 arquivos instaláveis soma **80.897.298 bytes**.
As seis bases produzidas nesta execução (BMD/NAME/FTS/POI/ROUTE/SHARED)
somam **1.440.253 bytes**, apenas **1,78%** desse total. Os outros
79.457.045 bytes são arquivos-base herdados e catálogos/metadados de pacote.

| Conteúdo | Bytes | MB |
|---|---:|---:|
| BMD de visão geral herdado, `DECO/UR0/BMD.NDS` | 45.137.920 | 45,14 |
| JV herdado | 14.405.632 | 14,41 |
| 3D herdado | 11.714.560 | 11,71 |
| TMC herdado | 6.340.608 | 6,34 |
| NAME de visão geral herdado, `DECO/UR0/NAME.NDS` | 1.085.440 | 1,09 |
| Seis bases geradas para este candidato | 1.440.253 | 1,44 |
| Demais arquivos e catálogos | 772.885 | 0,77 |

Dentro das seis bases, BMD ocupa 638.069 bytes; POI, 660.053; FTS, 64.700;
ROUTE, 56.165; NAME, 14.509; SHARED, 6.757. Schema/tabelas auxiliares e
estruturas de índice continuam fazendo parte das bases, além dos registros
do bairro. Tamanho em disco não corresponde diretamente à memória em uso.

O ZIP inteiro ocupa 80.023.562 bytes: comprime pouco a mais porque boa parte
dos payloads já usa container ZV-zlib, com blocos cifrados. Ele inclui também
os relatórios de suporte. A diferença de tamanho é dominada pelo bootstrap,
não por uma explosão das vias/POIs do Cajuru nem pela duplicação L11/L9.

Enxugar o pacote requer estudar quais desses assets podem ser substituídos por
bases menores/vazias compatíveis, inclusive a visão geral. Ainda não validamos
a remoção de JV/3D/TMC/DECO no loader. Não apagar arquivos obrigatórios nem
confundir redução de armazenamento com ganho de FPS.

## Instalação autorizada e smoke no carro

Concluída em 2026-10-24, coleta local `vehicle/20261024T220539Z/` dentro do
workspace Cajuru. Os 21 hashes remotos conferiram com o artifact manifest.
O pacote do Paraná permaneceu intacto. O `sdk.ini` foi salvo localmente e
em backup com sufixo `.pre-cajuru-v10-20261024T220539Z` antes da troca atômica
de `path_mapdata` para `Geely_EX2_CAJURU_multilod_v10/`.

Navegação e SDK reiniciados: inicialização `init done: 0,true`, MapWindow em
`OPERABLE`, processos vivos e crash buffer sem alteração. Os descritores do
processo SDK confirmaram 11 arquivos abertos no novo pacote, incluindo BMD,
SHARED, NAME, FTS, POI e ROUTE. Screenshot mostrou geometria e nome de rua,
com o diálogo de retomada da rota anterior ainda aguardando escolha do usuário.
Não iniciamos guidance nem descartamos a rota salva automaticamente.

O APK e os dados privados não foram limpos ou substituídos. Logs, screenshot,
backup e `install-report.json` ficam somente no workspace ignorado pelo Git.
Isto comprova instalação, inicialização e renderização inicial observada;
não comprova navegação em movimento, busca dos 41 POIs, seleção de LOD por
zoom ou melhora de performance. A restauração consiste em repor o `sdk.ini`
salvo e reiniciar navegação/SDK; o mapa anterior continua disponível.
