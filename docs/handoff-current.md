# Handoff após revisão e correções — 2026-10-20

## Restrições nativas ROUTE — investigação aberta, 2026-10-25

Leia [o handoff do parser, APIs nativas e próximo experimento](native-turn-restrictions-handoff-2026-10-25.md).
O grafo OSM agora emite transições diagnósticas em `route-model.json`, mas
nenhum writer nativo de restrições foi implementado ou validado.

## Correção de geometria ROUTE-AUX — 2026-10-25

Leia [a causa dos segmentos ausentes e os A/B no SDK](route-aux-missing-shapes-2026-10-25.md).
O writer declarava marcador2 mesmo para links sem row AUX; o leitor nativo
retornava somente um endpoint, e o consumidor recusava o segmento. Links retos
omitidos agora usam marcador0 com sua flag de extensão obrigatória. O candidato
AVD corrigido zerou os retornos count1, e os sete blobs do writer conferem
byte a byte com esse candidato. A evidência permanece específica ao runtime
e pacote testados; estados do pipeline e novos outputs não são promovidos.

## Achados de performance BMD — 2026-10-24

Leia [fragmentação e níveis de detalhe do Paraná](bmd-lod-performance-2026-10-24.md)
e a [evidência agregada](evidence-bmd-lod-parana-2026-10-24.json).
O mapa real observado contém 6.260.680 features em 2.518 tiles, quase todas
com dois vértices, e anuncia um único nível BMD para toda a escala. O documento
localiza a geração/assembler responsáveis, separa FRC de LOD, propõe etapas
de correção e registra limites e validações ainda pendentes. Não foi aplicado
um patch LOD no pipeline nem instalado outro mapa nesta documentação.

## Estado do mapa Paraná

O usuário autorizou interromper/limpar a tentativa estadual após a revisão.
O processo já havia terminado quando foi inspecionado. A limpeza removeu somente
`examples/work/neusoft-parana-v9/artifacts`, `.staging` e `state`.
Os inputs compartilhados de Curitiba/Israel, código e pacotes no carro foram
preservados. Não foi gerado outro mapa Paraná nem instalado candidato nesta rodada.
O exemplo estadual continua experimental: usa bbox, não polígono administrativo.

## Correções da revisão

- O fingerprint de cache inclui os fontes Python de todos os subpacotes do
  forge. Alterar um codec importado agora invalida `--resume`, evitando saída
  antiga após um fix em BMD/AUX/adapters.
- POI tem seleção explícita de fonte e mapeamento de categorias. O executor
  registrado foi testado desde PBF sintético até registros POI numéricos.
  Paraná agora usa `source = "osm"`, sem referência aos POIs curados de Curitiba;
  Na revisão inicial, ambos os mapeamentos foram esvaziados para bloquear IDs
  especulativos. A etapa posterior abaixo resolveu esse bloqueador com 18
  categorias nativas evidenciadas, sem afirmar cobertura estadual concluída.
  Curitiba mantém seu perfil curado de compatibilidade.
- Tiles sem geometria AUX selecionada voltam a ser rejeitados por padrão.
  `empty_aux_policy = "retain_first_experimental"` é opt-in explícito, registra
  contagem/IDs dos tiles afetados e permanece sem validação nativa/SDK.
- `pipeline.required_capabilities` recusa capacidades ausentes antes de criar
  workspace. Exigir endereços/restrições nativas não produz um pacote reduzido
  silenciosamente; essas capacidades continuam pendentes.
- O perfil estadual usa somente país/estado e deixa cidade/CEP sem atribuição.
  O writer recusa critérios contraditórios e registros municipais antigos.
  Isso não resolve municípios por rua nem ruas homônimas entre cidades.
- O compiler Python de restrições bloqueia transições desconhecidas/condicionais
  e recusa via-way/missing-via no modo padrão. Isso não codifica restrições no
  ROUTE.NDS nem altera o comportamento do SDK no carro.
- O validador offline aceita o `files[]` produzido por `pack_package_tree`, além
  de `output_hashes`. Rejeita manifesto vazio/ausente, caminhos inseguros,
  duplicatas e divergências de bytes/SHA. Nunca promove um resultado offline
  a evidência nativa, SDK, UI ou guidance.
- A documentação de recursos remove números regionais sem benchmark vinculado
  e esclarece que SIGKILL não garante cleanup/registro de falha. O RSS medido
  é o máximo histórico do processo, não um pico isolado de cada etapa. Não houve
  benchmark do mapa ou da central nesta rodada.
- O helper de rota deixou de inventar um resultado `SUCCESSFUL_LOCAL` a partir
  de distância em linha reta. Sem adaptador SDK real, retorna
  `UNSUPPORTED_SDK_ADAPTER`. A assinatura é mantida, mas consumidores que
  esperavam sucesso sintético devem tratar esse status como pendência.

## Pendências reais

| Área | O que falta |
| --- | --- |
| Busca estadual | Spatial join municipal, nomes por localidade e teste da hierarquia reduzida no Neusoft |
| POI | 18 categorias de membership único configuradas com evidência; faltam memberships múltiplos, subtipos, cobertura e aceitação nativa; geometria completa de relações não é garantida |
| Endereços | Implementar `address.compile` e integrar seus artefatos ao assembler ativo; o writer em `layers/address.py` ainda não é chamado pelo DAG |
| Restrições | Codificar e validar regras no ROUTE/AUX nativo, incluindo condições e via-way |
| SDK | Adaptador real, carregamento de candidato por hash, callbacks e GPS sincronizado |
| UI/carro | Instalação e testes do novo candidato, busca, rotas e depois guidance; evidência histórica vale só para os hashes registrados |
| Escala estadual | Novo build após as correções e auditoria dos outputs completos; nenhum foi feito nesta rodada |

O empacotamento usa exclusivamente `layers/package_assemble.py` através do
executor `package.assemble`; o assembler paralelo `layers/package.py` e sua
suíte isolada foram removidos por não terem consumidores de produção. A decisão
para `address_doc` é mantê-lo fora do contrato do assembler ativo até que
`address.compile` produza e valide seus artefatos; a presença do writer em
`layers/address.py` não significa que endereços sejam incluídos no pacote. Os
gates nativo/dispositivo não estão automaticamente concluídos por testes Python
ou hashes.

Consulte [validação](validation.md), [SDK pendente](evidence-sdk-route-harness.md)
e [hierarquia e restrições](state-admin-and-route-limits.md). Os detalhes de
[reparo do pipeline](pipeline-repair-2026-10-20.md) cobrem cache/POI/AUX e o reset.

## Verificação desta rodada

```sh
.venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Resultado: **172 testes passaram, sem skips**, em 2,531 segundos no ambiente
local com dependências opcionais disponíveis. `git diff --check` passou.
Os testes usam arquivos temporários, PBF sintético válido e SQLite de teste;
incluem o executor POI registrado e o manifesto produzido pelo empacotador.
Não executam build estadual, native SDK no carro nem instalação. Nenhum commit
ou push foi feito nesta rodada.

Após a suite, foi conferido por leitura de configuração e validação de perfil
sem IO de mapa que Paraná rejeita o mapeamento vazio com o erro explícito
`OSM POI mode requires explicit osm_tag_categories`; Curitiba continua aceito.
Não foi tentado um build para demonstrar esse bloqueio.


## Atualização — categorias POI nativas e desbloqueio do preflight estadual

O item de categorias foi resolvido offline, sem gerar outro Paraná e sem acessar
ou alterar o carro. A evidência está em [categorias nativas](poi-native-categories.md)
e no [catálogo auditável](poi-category-evidence.json). Os IDs/labels vieram do
catálogo nativo de `POI.NDS` e os pares caminho FTS completo ↔ categoria inteira foram conferidos por
join `poiId` nos dois seeds originais de Israel, identificados por SHA-256.
A tradução de tags OSM para esses labels é política semântica explícita, não
uma observação de tags OSM dentro do mapa de Israel.

- `examples/neusoft-parana-v9.toml` agora declara 18 tags/categorias, usando o
  perfil `neusoft-israel-poi-single-membership-v1` com hash do catálogo fixado.
  Postos, farmácias, restaurantes, cafés, estacionamentos, hotéis, museus e
  outras categorias de membership único estão incluídos.
- Foram encontrados 41 candidatos; os 23 com memberships múltiplos permanecem
  fora da configuração ativa. Hospitais, supermercados e fast food estão entre
  eles. Carregadores genéricos não estabelecem o subtipo normal/rápido observado
  no FTS e também permanecem excluídos. Não se atribui tudo a `1006` nem se usa
  o mapa especulativo do compiler standalone.
- `nds_forge/layers/poi_categories.py` valida identidade/hash, tags permitidas
  e os pares completos. O assembler ativo também compara os hashes dos seeds
  POI/FTS verificados com os hashes conhecidos da evidência antes de copiá-los
  ou resolver secrets. Um manifesto arbitrário com hashes autoatribuídos não
  autentica a taxonomia.
- `poi-records.json` inclui os mapas completos, identidade/hash da evidência,
  fonte OSM e limites. As origens POI/FTS do pacote deixam de afirmar dez POIs
  curados quando a fonte efetiva é OSM; mantêm o hash do artefato compilado e a
  identidade da evidência usada.

A regressão usa PBF válido temporário com uma entidade para cada uma das 18
tags habilitadas e duas sem suporte. O executor registrado produz 18 registros,
rejeita as outras duas e mantém estado `unvalidated`. Os mesmos registros passam
pelos writers efetivamente chamados em `package.assemble`, com schemas SQL
sintéticos: consulta FTS por nome encontra os 18 e cada caminho FTS mantém o
inteiro correspondente no POI. Há negativos para perfil/hash desconhecidos,
tag fora do catálogo, ID errado, caminho truncado e seed divergente; este último
falha no executor registrado antes de cópia/secret. Curitiba curado permanece
aceito.

Isso desbloqueia a configuração de categorias POI, **não** valida um pacote do
Paraná nem o SDK. O exemplo ainda usa bbox e hierarquia estadual reduzida.
A política AUX continua `reject` por padrão: nenhum fallback experimental foi
habilitado para ocultar um possível bloqueador posterior. Endereços, restrições,
acesso por link POI, testes nativos/UI e escala estadual continuam pendentes.


Verificação desta atualização em 2026-10-20: **179 testes passaram, sem skips**
na auditoria local independente (`.venv/bin/python -m unittest discover -s tests -v`).
Essa contagem é histórica. Na conferência atual de 2026-10-21, a suíte executou
**228 testes: 228 passaram, 11 foram ignorados**; `git diff --check` também
passou. Nenhum build estadual, SDK ou teste no carro foi executado. O preflight
de categorias Paraná e os hashes dos seeds locais foram conferidos separadamente
pela auditoria; os resultados Python não promovem o pacote a validado.
