# Começo do caminho sem conteúdo geográfico herdado

Branch: `feat/fresh-nds-schema-contract`, baseada no commit `6d9c64b`.
Estado: primeiro incremento implementado e exercitado offline. **Ainda não é
um pacote NDS instalável. O carro continua com o Cajuru v10.**
Evidência agregada: [evidence-fresh-schema-2026-10-24.json](evidence-fresh-schema-2026-10-24.json).

## Objetivo e distinções necessárias

O objetivo é gerar mapas com dados OSM e metadados explícitos do nosso perfil,
sem copiar geografia, registros de catálogo ou páginas do mapa original.
Precisamos continuar falando o formato que o SDK entende: schemas, nomes de
tabelas, contratos binários e códigos nativos não desaparecem por mudar a região.

Este incremento separa **contrato estrutural** de **conteúdo**. Os schemas
observados ainda têm proveniência no formato original, exportados do pacote
Cajuru v10. Portanto não afirmamos uma especificação NDS independente, clean
room ou um build completo sem qualquer dependência histórica do original.
O novo construtor usa só o contrato DDL, sem abrir o mapa de referência.
O teste remove o banco de origem antes de construir o destino.

## Dependência real encontrada

O assembler v9 está fixado em 21 caminhos e chama `_copy_bootstrap_assets`
para camadas auxiliares, além de copiar schemas e metadados. A etapa de
container também mantém uma lista de nove assets ZV estáticos.
Trocar apenas os blobs BMD/ROUTE ou apagar arquivos grandes não elimina
essas dependências.

A inspeção do PRODUCT do Cajuru mostrou 362 linhas em `globalGatewayTable`,
42 em `updateRegionNameTable`, 42 em `updateRegionSetNameTable` e 229 em
`bBlockCompVersionTable`. O SHARED conserva tabelas de formatos de endereço,
prefixos e timezone. As contagens não classificam todos esses registros como
geografia: alguns são metadados de compatibilidade. Elas demonstram que há
conteúdo que não foi regenerado pelos compiladores OSM.

REA: `ev_205d80da2928b123808d455b1be8807fd6a7dd3db464104c44abc36611aa15dd`;
PRODUCT inspecionado com SHA-256
`d861ea6dc4e550efb5d889f958fe1057c31fcfabfa5fb4c3cf8c03809d1d1c28`.
Essa evidência estrutural não prova requisitos do loader para bancos vazios.

## Código novo e execução

- `nds_forge/schema_contract.py::export_contract`: lê DDL, exclui objetos
  internos/shadow gerenciados pelo SQLite e exporta definições com identidade
  da origem. Não exporta registros nem valores de metadados das tabelas.
- `create_empty_database`: cria arquivo novo a partir do contrato, verifica
  que cada tabela lógica está vazia e executa `integrity_check`. Publicação
  exclusiva; destino existente não é substituído. Módulo ausente falha sem
  publicar um banco parcial. Não instala ou carrega extensões automaticamente.
- `tools/nds_schema.py`: CLI offline para exportar contrato e criar banco.
- `tests/test_schema_contract.py`: garante ausência de dados herdados,
  independência do arquivo de origem, criação de FTS/shadow pelo SQLite,
  rejeição de DML/dados inesperados e falha limpa de módulos ausentes.

Exemplo genérico, com arquivos de referência externos autorizados:

```sh
.venv/bin/python tools/nds_schema.py export /caminho/base.sqlite contrato.json
.venv/bin/python tools/nds_schema.py create contrato.json novo.sqlite
.venv/bin/python -m unittest discover -s tests -p test_schema_contract.py -v
```

O CLI não é uma etapa integrada ao registry e não anuncia pacote pronto.
Contratos reais extraídos ficam somente no workspace ignorado pelo Git;
nenhuma definição extraída de banco proprietário foi incorporada como fixture.

Execução real em `examples/work/cajuru-fresh-v11/`:

- Exportados contratos para os 18 bancos SQLite do layout observado.
- **17 bancos recriados**, com zero registros herdados e integridade válida.
- Total desses schemas SQLite vazios: **1.789.952 bytes**. Não comparar esse
  número a um mapa completo nem prometer tamanho final.
- **FTS bloqueado:** `no such module: spellfix1`. Há seis tabelas virtuais
  desse módulo no contrato FTS, além de FTS4/FTS4aux. Não foram removidas ou
  substituídas silenciosamente; nenhum FTS parcial foi publicado.
- Em outro diretório, dois bancos novos foram preenchidos pelos escritores
  existentes usando exclusivamente os artefatos OSM do Cajuru: BMD com quatro
  tiles e ROUTE com sete tiles MAIN e sete AUX. Integridade conferida.
- Esses bancos medem 106.496 e 147.456 bytes SQLite, respectivamente. Ainda
  faltam seus metadados semânticos e o restante do pacote.

Os relatórios locais são `scaffold-report.json` e
`fresh-osm-layers-report.json`. Scripts da execução ficam em `tools/` desse
workspace. Schemas em `contracts/`; bancos vazios em `fresh-schema-only/`;
camadas povoadas em `fresh-osm-layers/`. Os mapas de referência são somente
oráculos de pesquisa para exportar estrutura, não inputs de geografia.

## Próximas implementações para um pacote nosso

1. **FTS:** fornecer um backend com `spellfix1` compatível ou demonstrar pelo
   consumidor quais tabelas são opcionais. Criar bancos novos sem omissão
   silenciosa. Não copiar o FTS anterior para contornar a dependência.
2. **ROOT/PRODUCT:** escrever do zero supplier/version/productDb, região,
   building blocks e suas versões/metadados, cobertura e locale. Os writers
   atuais só modificam partes de um catálogo existente; não bastam para um
   catálogo vazio. `globalGatewayTable` deve ser gerada pelo grafo ou ficar
   vazia sob contrato validado, nunca herdada por conveniência.
3. **SHARED/NAME/POI:** gerar a região, país, timezone, convenções de endereço,
   nomes e taxonomia de busca a partir de configuração explícita. As funções
   atuais usam UPDATE/DELETE e assumem linhas auxiliares existentes; verificar
   cada precondição antes de reutilizá-las em bancos novos.
4. **DECO:** gerar visão geral somente com dados OSM/recorte declarado, em
   níveis e metadados próprios. Nunca recolocar a visão geral estrangeira.
5. **Camadas opcionais:** declarar ausência ou criar schemas vazios para
   JV/3D/TMC/EXTTMC/SPEECH, conforme o loader permitir. Um arquivo vazio ou uma
   tabela vazia não equivale a funcionalidade suportada. Confirmar relações
   entre buildingBlockTable, índices e arquivos.
6. **Assembler novo:** perfil dedicado, manifest de origem exclusivamente
   gerada/derivada de inputs próprios, metadata e ROOT.NDK produzidos do zero,
   chaves externas e containers verificados. Não alterar o modo v9 já aceito.
7. **Validação:** joins, nomes, cobertura, ausência de resíduos, decode dos
   containers, depois loader/SDK em ambiente isolado e finalmente teste no
   carro com backup. Nenhuma dessas últimas etapas foi executada neste começo.

Esta execução foi inteiramente offline, sem downloads de extensões, sem
instalação e sem mudança no dispositivo. **259 testes passaram**, Ruff e Mypy
passaram. A biblioteca SQLite local é suficiente para 17 schemas, não para
completar o FTS observado. Isso é um requisito concreto de continuação, não
evidência de que o SDK possa dispensá-lo.
