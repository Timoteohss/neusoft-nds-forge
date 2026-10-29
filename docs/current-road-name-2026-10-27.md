# Nome da rua atual na navegação

Correção implementada no Forge e validada no motor real e na UI do veículo em
27/10/2026. O usuário também confirmou em percurso. Não implica validação de
todos os mapas ou correção do posicionamento/rumo do veículo.

## Formato necessário

Busca, labels BMD e nomes usados pelo roteamento são vínculos distintos.
`routingAuxTileTable.nameAttributeLayer` anteriormente continha somente números
`0xB6`, mesmo quando NAME/FTS/BMD tinham nomes.

- Nome de rua: atributo **0x96**, valor u32 = NAME namedObjectId. Collector nativo
  mapeia para kind 0, consumido por `nds_rt_getNamesOfLink`.
- MAIN link index → refType **2**, ambas as direções.
- Link marker-2 → ordinal denso da geometria AUX → refType **4**.
- Não usar o índice MAIN onde o getter consulta o ordinal AUX. Contar também as
  geometrias de vias sem nome: elas ocupam posições nesse espaço.
- Adicionar o mapa de nomes type 4 habilita o caminho AUX também para números.
  Espelhar as faixas `0xB6` type 2 nesse espaço (type 4), sem alterar as originais,
  para não perder números que já eram consultáveis.

## Implementação

- `nds_forge/layers/route_attribute_compiler.py`: `compile_link_names` faz join
  OSM way id exato → nome → NAME id existente; recebe a ordem real de linhas AUX.
  `mirror_aux_house_numbers` preserva as consultas de número após habilitar AUX.
- `route_aux.py`: combina mapas de nome e de números; cobre fallback retain-first;
  emite hashes dos inputs de nomes e contagens no manifesto.
- `steps.py`: `routing.aux` depende explicitamente de `osm.clip` e `names.compile`.
- `tests/test_route_link_names.py`: ordinais esparsos, ambas as direções, falta de
  NAME, conflitos, geometria sem nome, fallback e números espelhados.

## Validação executada e limites

Pacote Curitiba v10: 56247 links nomeados, 22646 sem nome no OSM. Todos os 103
tiles têm nomes compilados. Geometria MAIN/AUX e demais tabelas/colunas comparadas
à baseline e preservadas. Consultas nativas em amostra que cobre os 103 tiles,
ambas as direções e ruas da investigação: **850/850 nomes corretos** e **850/850
consultas de números idênticas à baseline**. O motor publicou o nome da rua e a
UI mostrou o nome; usuário confirmou navegação em percurso.

Registros privados reproduzíveis e hashes ficam fora deste repositório:
`geely-ex2-investigation/neusoft-maps/native/current-road-name-20261027/FINDINGS.md`.
Não adicionar bases, logs do veículo, APKs ou chaves ao Git.

Essa validação não cobre todas as possíveis faixas de atributos OEM, placas
administrativas, traduções ou nomes direcionais. Vias sem nome permanecem sem
nome. Não modifica o filtro de localização nem o encaixe na rua.
