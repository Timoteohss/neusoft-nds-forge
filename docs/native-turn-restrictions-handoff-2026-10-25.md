# Handoff: native ROUTE turn restrictions

Data da sessão: 2026-10-25. Este handoff registra o estado real da investigação
para continuação por outro agente. **Não há ainda um encoder nativo de
restrições OSM, nem evidência de que o SDK aplique as transições diagnósticas do
Forge.**

## Alvo e arquivos persistidos

Biblioteca alvo:

`../geely-ex2-investigation/neusoft-maps/native/libNdsLib.so`

SHA-256: `c34a7e7a6201cb469bd6c9b2d62d36ef606f12a369ce3027969647a34a970e55`

O pacote instalado no AVD `emulator-5554` contém uma cópia com o mesmo SHA-256.
O AVD é `arm64-v8a`. O app Neusoft está instalado, mas não foi aberto nem
alterado nesta sessão.

Evidências binárias reproduzíveis ficam em:

`../geely-ex2-investigation/neusoft-maps/native/route-transition-research-20261025/`

- `transition-api.asm`: código em torno das APIs exportadas de transição.
- `route-tile-parser.asm`: parser principal `0x10e6a0`.
- `bit-reader-helpers.asm`: helpers de leitura consultados pelo parser.
- `section-samples.json`: prefixos brutos dos índices 3, 4 e 5 em cinco tiles
  originais. A cópia SQLite usada para coletar esses bytes era temporária; o
  JSON preserva o hash e as amostras necessárias para retomar a análise.

Comandos para reproduzir os disassemblies, usando apenas o binário cujo hash foi
confirmado:

```sh
xcrun llvm-objdump -d --start-address=0xf82e8 --stop-address=0xf9404 \
  ../geely-ex2-investigation/neusoft-maps/native/libNdsLib.so
xcrun llvm-objdump -d --start-address=0x10e6a0 --stop-address=0x10f75c \
  ../geely-ex2-investigation/neusoft-maps/native/libNdsLib.so
xcrun llvm-objdump -d --start-address=0x1d9508 --stop-address=0x1d9f20 \
  ../geely-ex2-investigation/neusoft-maps/native/libNdsLib.so
```

## O que está comprovado

1. O header ROUTE deste modelo tem 10 offsets. No código e na tabela
   `SECTION_NAMES` de `geely-ex2-investigation/neusoft-maps/tools/route_geometry.py`,
   os índices 3, 4 e 5 são `downlinks`, `section_4` e `section_5`. **O índice 3
   não é uma tabela de conversões proibidas.** O parser em `0x10eaa8` lê essa
   seção com o marcador `0x8c`; os tiles amostrados mostram contagem seguida de
   registros de downlink.
2. O parser também consome as seções nos índices 4 e 5, mas elas continuam sem
   nome semântico seguro. Exemplos dos bytes e limites estão em
   `section-samples.json`; não assumir que qualquer uma delas seja a tabela de
   restrições sem correlacionar com as APIs nativas.
3. A biblioteca exporta:

   | API exportada | endereço da exportação | corpo observado |
   |---|---:|---:|
   | `nds_rt_getTransitions` | `0x939c0` | `0xf82e8` |
   | `nds_rt_getTransitionAttributes` | `0x939c8` | `0xf8d34` |
   | `nds_rt_getContinuedTurnRestrictions` | `0x939d0` | `0xf91c4` |
   | `nds_rt_getAttributeTimeConditions` | `0x939d4` | `0xf92d0` |

4. `nds_rt_getContinuedTurnRestrictions` (`0xf91c4`) desempacota de seu
   identificador um índice de link e uma identidade de tile/nível, resolve um
   `RoutingTile` em cache e chama `0x112804` com o link, sentido, leitor de bits
   e um vetor de saída.
5. Em `0x112804`, o código valida o índice contra a contagem de links do tile,
   percorre referências de links e procura um registro cujo campo satisfaça
   `(value & 0x1ffe) == 0x9a`. Ele também compara bits 13–14 com um estado de
   direção/opção e materializa identificadores compactados no vetor. Isso
   comprova uma estrutura interna especial associada a restrições continuadas;
   ainda não prova que `0x9a` seja a representação de uma relação OSM `no_*` ou
   `only_*`.
6. `nds_rt_getTransitions` enumera transições conectadas ao link e usa caches e
   objetos `RoutingTile`. Seu nome sozinho não prova que a função seja o leitor
   de proibições de conversão. A API `getContinuedTurnRestrictions` é a pista
   direta a seguir.

## O que não funcionou e por quê

O binário existente
`../geely-ex2-investigation/neusoft-maps/tools/nds_route_probe` usa o parser
nativo em processo separado e aceita o cabeçalho. Ao tentar `--full-all` em um
tile original denso (`8389480`), o processo terminou com `SIGSEGV` em
`main+1180` (`fault addr 0x10`). O próprio `validate_route_native.py` já
documenta que o scratch da sonda não implementa o contexto de atributos
flexíveis; esse tile tem o offset 8 presente. Portanto:

- sucesso de header é evidência apenas do header;
- essa execução não decodificou a seção 5 nem chamou os getters de restrição;
- não repetir `--full-all` em tiles com atributos flexíveis nessa sonda sem
  construir o contexto que falta.

O provider de decompilação do REA não estava configurado (Ghidra/Hopper/IDA/Kuna
indisponíveis); os arquivos `.asm` persistidos foram gerados por
`xcrun llvm-objdump`. A seção 5 tem contagens pequenas e linhas com referências
de link, mas os bytes sozinhos ainda não fornecem o significado dos campos.

## Estado do patch no NDS Forge

`nds_forge/layers/route_graph.py` lê relações OSM, protege nós `via` contra a
junção de segmentos grau-2 e grava em `route-model.json` transições locais
diagnósticas para o subconjunto que `route_rules.py` conhece. Os IDs de links
direcionais desse JSON são IDs virtuais do modelo, não IDs persistidos no
`ROUTE.NDS`. O encoder `route_codec.py` **não** serializa essas transições. O
SDK não recebe nem aplica esse modelo diagnóstico.

As relações via-way e condicionais não têm codificação nativa. Relações junto a
bordas de tile, relações incompletas no recorte e restrições com múltiplos
membros `from`/`to` também precisam de tratamento explícito. Este incremento é
uma ferramenta de inspeção e preservação de topologia, não uma correção de
roteamento embarcado.

## Próximo experimento recomendado

1. Criar uma cópia instrumentada da sonda C existente. Fixar biblioteca,
   offsets e SHA-256; não apontar para outro build sem recalcular os offsets.
2. Obter um `RoutingTile` plenamente parseado. O parser direto devolve o objeto
   de tile, mas tiles com atributos flexíveis exigem o resolver/allocator e
   contexto de banco corretos. Reaproveitar o contexto real em processo isolado
   ou construir o scratch completo; não chamar o getter com struct adivinhada.
3. Resolver a assinatura da API pelo chamador no binário e pela ABI do wrapper
   C/Java. Registrar os argumentos embalados (tile, nível, link e direção),
   capacidade e layout do vetor de saída.
4. Escolher um cruzamento original com os links de entrada/saída já resolvidos
   por `tools/route_geometry.py` e confirmar os IDs devolvidos por
   `getTransitions` e `getContinuedTurnRestrictions`.
5. Correlacionar a consulta com os registros das seções 4/5. Decodificar
   primeiro sem alterar bytes. O parser `0x10f244..0x10f380` lê a seção no
   offset de índice 5 como contagem e estruturas com IDs de link; decodificar
   cada campo seguindo os helpers chamados, sem inferir endianness/largura pelo
   prefixo apenas.
6. Só então construir duas fixtures sintéticas idênticas, uma sem regra e uma
   com uma transição bloqueada. O parser nativo deve aceitar ambas; getters e
   consulta de rota devem divergir somente na transição configurada.
7. Implementar writer e decoder independentes em `route_codec.py`, mantendo
   round-trip byte/semântico e testes diferenciais contra o reader nativo. Só
   anunciar suporte depois de consulta de rota nativa que prove o desvio.

Ferramentas de partida:

- `../geely-ex2-investigation/neusoft-maps/tools/nds_route_probe.c`
- `../geely-ex2-investigation/neusoft-maps/tools/validate_route_native.py`
- `../geely-ex2-investigation/neusoft-maps/tools/route_geometry.py`
- `../geely-ex2-investigation/neusoft-maps/native/route-samples/`
- `nds_forge/layers/route_rules.py`
- `nds_forge/layers/route_graph.py`
- `nds_forge/layers/route_codec.py`

## Segurança operacional e estado

Não houve instalação no carro, alteração de mapas do carro ou escrita no app.
Na tentativa isolada no AVD, foram enviados somente o binário da sonda, a
biblioteca e um tile a `/data/local/tmp`; remover esses arquivos após terminar.
Não reutilizar o comando de validação que pressupõe o caminho
`/system/app/NAVI/lib/arm64`, pois o AVD instala o APK em `/data/app`.

Esta sessão não executou testes da suíte, não implementou o writer nativo e não
mediu uma rota nativa com uma conversão proibida. O `git diff --check` foi a
única verificação executada no NDS Forge.
