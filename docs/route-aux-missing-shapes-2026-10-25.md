# ROUTE-AUX: geometrias de um ponto e saltos no meio da rota

Data: 25/10/2026. A correção pertence ao writer de dados ROUTE-AUX. O APK,
o GNSS e o grafo MAIN não precisam mudar para corrigir o defeito identificado.
A evidência abaixo usa o SDK real num AVD Android Automotive arm64, separado
do carro. Não promove o pipeline experimental a navegação validada em veículo.

## Defeito confirmado no writer

`nds_forge/layers/route_codec.py::encode_aux_sparse` gravava o marcador 2 para
todos os links MAIN, mas só gerava rows de geometria para os links retidos.
No Cajuru v10 havia 3.155 links, 639 rows AUX e 2.516 links sem row. Todos os
omitidos eram segmentos retos com exatamente dois endpoints.

O marcador 2 manda o leitor buscar geometria AUX. Quando a row não existe,
`nds_rt_getShapePoints` devolve **somente o endpoint final**, apesar de os dois
endpoints enviados serem diferentes. Não é uma redução legítima de uma reta
para dois vértices: a geometria fica com um único ponto, insuficiente para
representar um segmento. Um consumidor da `libna_onecore.so` recusa esse
resultado ao verificar count < 2. Os segmentos recusados deixam lacunas na sequência de geometria que o consumidor
recebe. Isso é compatível com os saltos observados entre quadras, mas a captura
não observou o buffer final desenhado na tela; portanto a ligação com cada salto
visual específico ainda é uma inferência, não uma validação de framebuffer.

O callback `TurnInfo` não é a lista de pontos da linha: ele contém manobras.
A escassez de pontos nesse callback não foi usada como prova desta causa.
A prova veio das chamadas nativas de geometria e dos A/B de dados abaixo. O A/B
confirma a correção da geometria retornada pelo SDK; a aceitação visual da rota
final ainda precisa de confirmação no renderer ou no carro.

## Fluxo nativo observado

Binário de referência: `libNdsLib.so`, SHA-256
`c34a7e7a6201cb469bd6c9b2d62d36ef606f12a369ce3027969647a34a970e55`.
Os endereços são offsets virtuais nesse ELF e não devem ser reutilizados com
outro hash.

1. `nds_rt_getShapePoints`, export `0x939b0`, corpo `0xf795c`, delega para
   `0xf6730`. O marcador interno2 escolhe o caminho de lookup AUX.
2. A falha no lookup chamado em `0xf6a24` toma o salto
   `0xf6a28 → 0xf6c68`. Esse salto passa depois do bloco que poderia inserir
   o endpoint inicial em `0xf6ba8..0xf6bc4`.
3. Se também não há geometria externa, o bloco `0xf6ce0..0xf6d28` acrescenta
   apenas o endpoint final. A API retorna count=1.
4. No consumidor `libna_onecore.so+0x1532548`, o teste
   `0x1532554: subs w9,w8,#2` seguido de `b.lo 0x1532588` retorna false para
   count=1. Não processa o link como um segmento válido.

O caminho nativo do marcador interno0 é explícito: `0xf6824..0xf6878` devolve
os dois endpoints fornecidos. Este é o marcador correto para os links retos
cuja geometria AUX foi omitida no perfil testado.

## A/B que separa o meio da rota do encaixe dos extremos

O primeiro controle acrescentou rows AUX somente para 19 links intermediários
capturados, mantendo o grafo MAIN e os links de encaixe dos extremos.

| Variante | Distâncias das três alternativas | Chamadas count=1 no consumidor `+0x1532548` |
|---|---|---:|
| Cajuru v10 original | 2.355,27 / 2.815,52 / 2.646,12 m | 19 |
| Acrescentar AUX dos 19 links intermediários | 2.355,27 / 2.815,52 / 2.646,12 m | 0 |

As distâncias permaneceram idênticas. Os 19 links passaram a fornecer
geometrias utilizáveis sem depender de corrigir o ponto inicial ou final.
Outros consumidores ainda viram sete retornos count=1 para links fora desse
conjunto: este A/B é isolado, não uma correção de todo o pacote.

O segundo A/B manteve as 639 rows AUX e mudou os marcadores dos 2.516 links
omitidos para0, com a codificação correta descrita abaixo.

| Captura completa | Chamadas ShapePoints | count=1 | count=2 | count=3 |
|---|---:|---:|---:|---:|
| Original | 187 | 108 | 32 | 47 |
| Marcadores0 corrigidos | 290 | 0 | 263 | 27 |

Todas as 108 geometrias degeneradas do original eram `[endpoint final]` de
links sem AUX. A variante corrigida não teve nenhuma geometria de um ponto.
Os conjuntos de chamadas diferem porque o SDK continua processando segmentos
que antes recusava; as contagens não devem ser tratadas como pareamento 1:1.

O SDK retornou sucesso local com distâncias 2.481,25 / 2.943,14 / 2.772,82 m.
O aumento de 125,98 m na primeira alternativa corresponde aos efeitos já
isolados do encaixe inicial (118,48 m) e final (7,50 m). Esse aumento não deve
ser usado para medir a correção das geometrias intermediárias; o controle dos
19 links acima mantém as distâncias e separa esse efeito.

## Codificação correta e tentativa inválida descartada

No modelo15, os marcadores têm tamanho variável:

| Caso | Bits emitidos | Significado |
|---|---|---|
| Link com row AUX | `10` | marcador 2 |
| Link reto sem row AUX | `000` | marcador 0 + extension-present=0 |

O decoder `0xefb34` lê os 2 bits em `0xefe14`. O marcador 0 vai para
`0xefe78 → 0xefeac → 0xeff58 → 0xeff64`, onde lê a flag de extensão. O
marcador 2 pula diretamente para `0xeff78` e não consome essa flag.

Uma primeira fixture trocou `10` por `00` no lugar. Ela era **inválida**:
consumia o próximo marcador como flag de extensão e desalinhava o parser,
fazendo o SDK retornar INVALID antes das consultas de geometria. Essa fixture
não é evidência contra o marcador 0 e não deve ser instalada ou reutilizada.
O parser Python da seção de geometria não detectava o erro porque não lê a
seção de marcadores. A fixture V2 reescreve a tabela inteira, acrescenta as
flags e ajusta os offsets posteriores; a seção de geometria fica byte-idêntica.

O writer foi corrigido para emitir2 somente para links retidos e `0+flag0`
para omitidos. Também recusa omitir polilinhas curvas ou links sem dois
endpoints distintos, evitando que o fallback descarte uma curva silenciosamente.
O caminho com todos os links retidos continua byte-compatível na tabela de
marcadores. O guard de offsets relativos uint16 das rows permanece necessário.

## Reprodução e evidências locais

As capturas ficam no projeto de investigação adjacente, fora deste repositório,
em `../geely-ex2-investigation/artifacts/avd-route-lab/`:

- `native-api-base-20261025/report.json`: alternativas do original.
- `native-geometry-api-base-20261025.jsonl`: chamadas originais; SHA-256
  `9849b6d29a437914a3f1c5a63572c4cb1260c6397da3a2aa2c1598c81d692d5f`.
- `native-api-added19-20261025/report.json` e
  `native-geometry-api-added19-20261025.jsonl`: controle dos 19 links; SHA-256
  do JSONL `8bc446814d2df9c87133e4b917bcaa4a1d3ae468e6d35b88e1bba8a9be165f6a`.
- `native-api-marker0-v2-20261025/report.json`: sucesso do candidato V2;
  SHA-256 `dd220d261fa23ea72e8a61914ae7dee41bd4876ddb6390287ba09ad8178da1a8`.
- `native-geometry-api-marker0-v2-20261025.jsonl`: zero count=1; SHA-256
  `a701a0c1b0e39e27b98efacb8c6d11d36f96334bbd8b4c16e1def3fc00747b27`.
- `onecore-shapepoints-consumer-20261025.asm`: teste count < 2 do consumidor.
- `marker0-v2-candidate/marker-fixture-validation.json`: preservação de MAIN,
  rows/seção AUX e roundtrip do container.
- `marker0-v2-forge-writer-comparison.json`: todos os sete blobs AUX produzidos
  pelo writer corrigido são byte-idênticos aos do candidato V2 testado no SDK.

Gerador da fixture V2: `../geely-ex2-investigation/neusoft-maps/tools/build_aux_marker_fixture.py`.
Os mapas, bibliotecas, chaves e capturas não foram copiados para o NDS Forge.
Não há execução nativa ou acesso ao carro nos testes Python.

Regressões sintéticas e checks relacionados:

```sh
python3 -m unittest tests.test_route_aux_policy tests.test_pipeline_contract tests.test_container_packaging -v
git diff --check
```

Os 43 testes passaram. Cobrem o bit de extensão em uma tabela mista, manutenção
do marcador 2 para retidos, IDs/shape das rows, rejeição de omissões inadequadas,
contratos de pipeline/cache e empacotamento. As etapas continuam
`experimental`, e a saída de um novo build continua `unvalidated` até receber
sua própria evidência. A prova AVD não substitui validar a linha final na UI,
direções de ruas, restrições de conversão ou orientação no veículo.
