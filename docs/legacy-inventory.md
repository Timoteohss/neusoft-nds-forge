# Inventário do pipeline Neusoft legado

Este inventário descreve o que existe em `geely-ex2-investigation/neusoft-maps/tools` e o que pode servir de referência para `nds-forge`. Ele não copia dados, templates, APKs, bibliotecas nativas, bancos de mapas ou chaves. Os caminhos abaixo são referências ao checkout legado local, não dependências de runtime do core.

## Resumo da cobertura

O pipeline legado comprovado termina hoje em três níveis diferentes:

1. **Recorte OSM municipal**: PBF regional + polígono local produzem `roads.geojson` e `source.json` (`tools/extract_curitiba_full.py`). O produto contém vias e tags dessas vias, não um modelo canônico completo de OSM.
2. **Camada cartográfica BMD**: vias clipadas viram tiles BMD e nomes (`tools/build_curitiba_geometry.py`, `tools/bmd_geometry.py`). A camada foi usada para renderizar a malha de Curitiba. Isso não gera roteamento completo.
3. **Busca prototípica e pacote derivado de template**: `tools/build_curitiba_package.py` atualiza BMD, NAME, FTS e POI, mantendo como base um pacote Neusoft de outra região. Isso foi suficiente para mapa visual, nomes pesquisáveis e dez POIs centrais curados; não é um empacotador regional genérico.

ROUTE tem parsers, encoders de fixture e grafos OSM experimentais. Os blobs principais do candidato municipal nível 13 passaram pelo parser nativo isolado, mas o conjunto não está validado para cálculo de rota no SDK. Restrições de conversão, acesso condicional e guidance não são compiladas. Não foi encontrado builder de intervalos de endereço nem ingestão completa de POIs.

## Scripts e dependências

| Script legado | Entrada → saída | Dependências externas | Estado e limites |
|---|---|---|---|
| `tools/extract_curitiba_full.py` | PBF regional + GeoJSON do polígono → `roads.geojson`, `source.json` | `pyosmium`, `shapely` | Implementação real para clip de vias; lista `ROAD_CLASSES` no próprio script. Preserva ID OSM de way e tags, mas converte linhas em partes recortadas. Metadados de origem ainda têm URL e relação 297514 fixos no manifesto; não exporta nós, relações, POIs, endereços ou restrições como camadas próprias. Sem etapa de download/resolução de fronteira. |
| `tools/bmd_geometry.py` | Coordenadas/linhas tile-local → codec BMD e primitivas de tile/bitstream | biblioteca padrão (`dataclasses`) | Codec parcial derivado do leitor nativo. Reutilizável com parâmetros explícitos; não é modelo de features, não gera catálogo, pesquisa ou pacote completo. |
| `tools/build_curitiba_geometry.py` | `source.json` + `roads.geojson` → `.bmd.bin`, `street_names.json`, `manifest.json` | `bmd_geometry.py` | Experimental e específico por política. Nível 11, shift 4, mapeamento highway→FRC e filtro pedestre são defaults no código. A divisão em tiles e name IDs são candidatos a extrair; as regras não devem virar defaults universais sem configuração. |
| `tools/build_curitiba_package.py` | Template NDS + blobs BMD + ruas/nomes → pacote candidato com BMD/NAME/SHARED/FTS/POI e catálogo atualizado | `sqlite3`, `cryptography` indiretamente via `pack_nds.py`/`unpack_nds.py`; arquivos privados externos | **Curitiba-only e template-bound.** Usa caminho `Geely_E245_ISR/db/NAV/UR439`, updateRegionId 439, tabelas/schema retirados do template e dez POIs codificados no módulo. Apaga/recria tabelas de busca/POI e campos administrativos, herda arquivos/camadas de outra região. Não portar como builder genérico. Pode servir como referência de schema, sob dados locais autorizados. |
| `tools/test_build_search.py`, `tools/test_fts_builder.py` | GeoJSON/nomes + bancos decodificados → montagem experimental de NAME/FTS/POI | `sqlite3` | Scripts legados de experimento, presos a caminhos de template e a Curitiba; não são suite independente nem CLI portável. Consultar apenas para entender schema. |
| `tools/build_curitiba_full_route.py` | PBF + polígono → ROUTE main/AUX por tile + manifesto | `pyosmium`, `shapely`, módulos locais BMD/ROUTE | Experimental. Preserva IDs de nós OSM e cria gateways de fronteira de tile. Compila grafo e alguns atributos; não compila turn restrictions, access condicional, guidance, faixas, pedágios ou sinais. Level 15 fixo no script. |
| `tools/build_curitiba_simplified_route.py` | PBF + polígono → grafo nível 13 com chains degree-2 fundidas, AUX e manifesto | `pyosmium`, `shapely`, módulos locais BMD/ROUTE | Experimental. Heurística de simplificação e merge; preserva formas em polilinhas e requer validação de SDK antes de qualquer alegação de rota funcional. Não é produto de navegação pronto. |
| `tools/build_route_fixture.py` | OSM XML local + metadados → fixture de um tile ROUTE/AUX e manifesto | biblioteca padrão | Fixture não implantável, restringe-se a um tile e bounds; mantém relações de restrição como evidência, mas não as codifica. |
| `tools/route_geometry.py` | blob ROUTE → header/interseções; encoder fixture parcial | biblioteca padrão + `bmd_geometry.py` | Codec parcial testado contra evidência do leitor. Sem restrictions, conditional access, turn guidance nem compilação comprovada de pacote. |
| `tools/route_aux_geometry.py` | blob AUX → header/geometrias; encoder de fixture | biblioteca padrão + codecs ROUTE/BMD | Parcial. A interpretação de rows/mapping deve seguir evidência nativa documentada; encoder experimental e ainda não equivale ao writer oficial. O offset relativo da geometria usa campo de 16 bits em bits (máximo 65.535 por início de row); isso é limite de campo, não limite geral de bytes do blob. |
| `tools/route_link_attributes.py` | coordenadas + tags/direção → atributos básicos do link | biblioteca padrão | Parcial. Velocidade fallback é hipótese do protótipo, não limite oficial/legal. Flags restantes, regras condicionais e turn restrictions em aberto. |
| `tools/build_route_package.py`, `tools/build_curitiba_full_route_package.py`, `tools/build_curitiba_route_no_aux_candidate.py` | blobs ROUTE + template/pacote Curitiba → candidato NDS com DBs e índices atualizados | `sqlite3`, `cryptography`, keys externas | Empacotadores de candidatos, todos vinculados a template Curitiba/ISR/UR439, IDs de catálogo e tabelas observadas. Produção de pacote não valida routing no SDK. O no-AUX não demonstrou que AUX seja dispensável devido a prontidão inconclusiva no experimento antigo. |
| `tools/pack_nds.py`, `tools/unpack_nds.py` | SQLite ↔ contêiner ZV-zlib criptografado | `cryptography` | Utilitário de storage, não de compilação. Requer key de mapa fornecida pelo operador (16 bytes); não adivinhar, embutir, copiar ou versionar chave. Entrada/saída usam criação exclusiva; tratar como adapter opcional de segredo. |
| `tools/inspect_route_tiles.py` | bancos ROUTE decodificados → contagens/índices | `sqlite3` + parser local | Diagnóstico offline condicionado a schema. |
| `tools/test_bmd_geometry.py`, `tools/test_route_geometry.py`, `tools/test_route_aux_geometry.py`, `tools/test_route_sdk_protocol.py` | fixtures/evidências retidas → regressões de codecs/protocolo | `unittest`, biblioteca padrão | Testes úteis de pesquisa, mas seus dados de referência não devem ser movidos para o novo repo sem revisão de licença/propriedade e sem necessidade. |
| `tools/validate_route_native.py`, `tools/validate_route_aux_native.py`, `tools/probe_route_sdk.py`, `tools/test_curitiba_route_package_on_device.py`, `tools/test_route_candidate.py` | fixtures/candidatos + dispositivo/SDK → resultados nativos | `adb`, binários C/probe e dispositivo; Python usa `socket`/`subprocess` | Fase opt-in separada. Não executar automaticamente no pipeline. Probes isolados de parser não provam cálculo de rota, e teste SDK não prova guidance. |
| `tools/deploy_curitiba_v4.py`, `tools/center_map.py`, `tools/render_route_on_screen.py` | candidato + comandos ADB/Thrift → estado de app/tela | `adb`, sockets, dispositivo | Automação de aparelho, hardcoded IP/portas/paths Curitiba. Fora do pipeline offline; não portar para execução padrão. |

No checkout legado não há `pyproject.toml`, `uv.lock` ou arquivo requirements. Dependências opcionais identificadas diretamente nos imports/uso são `pyosmium`, `shapely` e `cryptography`; codecs e scripts restantes dependem principalmente da biblioteca padrão. O core novo não deve herdar o ambiente implícito do legado.

## Acoplamentos e material que não deve ser levado

- Os builders de package apontam para `Geely_E245_ISR`, `NAV/UR439` e `updateRegionId=439`, inclusive em SQL e seleção de key index. Isso é binding de template/região, não uma regra do formato universal.
- Nomes administrativos Brasil/Paraná/Curitiba, Morton center de Curitiba e `CURITIBA_POIS` são dados/regra regionais codificados no builder.
- `pack_nds.py` e `unpack_nds.py` aceitam key path configurável, mas o key-index e as keys ficam em diretórios privados do checkout de investigação. Não incluí-los no repositório nem em fixtures.
- Não copiar `decoded/`, `live-test-source/`, `private/`, `assets/`, `native/`, `jadx/`, pacotes NDS, APK, logs do veículo, dumps, database proprietary, catálogo de fonte de fornecedor ou artefatos extraídos proprietários. Use referências externas de caminho/documentação; core opera com artefatos do usuário em diretórios locais.
- Atividades do APK/EV/tráfego e instalação/configuração do carro não são etapas do compilador de mapas. TMC herdado é camada/regional de tráfego legada, não prova cobertura de feed no Brasil; não integrar como se fosse dado OSM.

## Adaptadores recomendados

1. **`osm.pbf.local`**: adapter genérico, sem rede, valida arquivo e calcula SHA-256; declara fonte/proveniência apenas quando o operador fornecer. O download HTTP é outro adapter `osm.pbf.url`, com consentimento explícito de rede, URL/checksum e licença documentados.
2. **`region.polygon.local`**: lê polígono local GeoJSON, valida geometria e grava sua origem/hash/CRS. ID de relação OSM é metadado até que uma etapa explícita o resolva; não inferir polígono por bbox.
3. **`osm.clip.features`**: PBF + polígono/bbox → features clipped com IDs, tags e lineage. Esta é a única operação legada que pode ser adaptada imediatamente para além da engine de fixtures. A saída atual de roads é útil para BMD, mas não substitui representação de nós/relações para routing, endereços e regras.
4. **`osm.normalize`**: modelo canônico intermediário compartilhado. Deve preservar OSM IDs/tags originais e tipar vias/nós/relações, sentidos/acesso, `name`/aliases, POIs, números de endereço e restrições. Cada entidade/capacidade pode estar ausente com contagem explícita; salvar tags brutas não significa que regra foi compilada.
5. **Adapters de camada**: BMD, NAME/FTS, POI, endereços, ROUTE/AUX, catálogo/regions e pacote precisam ser consumidores independentes do modelo canônico e de uma binding declarada de schema/template. Sem adapter validado, etapa deve permanecer planned/experimental.
6. **`nds.crypto`**: plugin opcional que recebe key via secret provider/path configurável fora do workspace; valida acesso e tamanho, nunca registra bytes. Separar crypto do builder relacional de catálogo.

## Contrato de invocação para adapters legados

O core não deve importar os módulos `tools/` nem adicionar `neusoft-maps` ao `PYTHONPATH`. Um adapter externo chama subprocesso com `argv` (sem shell), cwd e paths explícitos. Cada adapter declara `supported_region_scope`, capabilities e versão. Inicialmente, o legado Curitiba fica desabilitado por padrão e aceita somente a configuração do fixture de Curitiba documentada.

Para o extrator legado, a chamada observada é:

```text
python tools/extract_curitiba_full.py <local-pbf> <local-boundary.geojson> <staging-dir>
```

Para geometry builder:

```text
python tools/build_curitiba_geometry.py <staging-dir-com-source-e-roads> --target-dir <staging-dir/geometry>
```

Esses comandos não podem ser rotulados como genéricos sem remover os metadados 297514/URL fixa e parametrizar política de vias, nível e shift. O adapter tem de comparar os hashes/arquivos esperados, verificar status, counts e licença, e publicar saída só após validação. Não reinterpretar mensagens stdout como status de sucesso se o processo falhar.

## Referências verificáveis no checkout legado

- `/Users/timoteosousa/apps/geely-ex2-investigation/neusoft-maps/tools/extract_curitiba_full.py`
- `/Users/timoteosousa/apps/geely-ex2-investigation/neusoft-maps/tools/build_curitiba_geometry.py`
- `/Users/timoteosousa/apps/geely-ex2-investigation/neusoft-maps/tools/bmd_geometry.py`
- `/Users/timoteosousa/apps/geely-ex2-investigation/neusoft-maps/tools/build_curitiba_package.py`
- `/Users/timoteosousa/apps/geely-ex2-investigation/neusoft-maps/tools/build_curitiba_full_route.py`
- `/Users/timoteosousa/apps/geely-ex2-investigation/neusoft-maps/tools/build_curitiba_simplified_route.py`
- `/Users/timoteosousa/apps/geely-ex2-investigation/neusoft-maps/tools/route_geometry.py`
- `/Users/timoteosousa/apps/geely-ex2-investigation/neusoft-maps/tools/route_aux_geometry.py`
- `/Users/timoteosousa/apps/geely-ex2-investigation/neusoft-maps/tools/route_link_attributes.py`
- `/Users/timoteosousa/apps/geely-ex2-investigation/neusoft-maps/tools/pack_nds.py` e `unpack_nds.py`
- `/Users/timoteosousa/apps/geely-ex2-investigation/neusoft-maps/brazil/curitiba-full/README.md` — proveniência, licença ODbL, estado v6/v9 e limites conhecidos.
