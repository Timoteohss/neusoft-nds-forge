# Linha de Base de Recursos e Execução Retomável (Issue #35 / [5.4])

Este documento descreve os mecanismos de medição de recursos, processamento em lotes com retomada atômica (*atomic resume*) e invalidação por fingerprints, para a Issue [#35](https://github.com/Timoteohss/neusoft-nds-forge/issues/35). A presença desses mecanismos não comprova um orçamento regional de recursos nem resistência a falhas abruptas.

---

## 1. Mecanismo de Retomada Atômica e Fingerprints

O orquestrador do pipeline (`nds_forge/pipeline.py`) garante isolamento e integridade nas seguintes condições:

### 1.1 Invalidação por Mudança de Entrada
- O `fingerprint` de cada etapa combina:
  - O hash canônico da configuração do projeto (`config.fingerprint_data()`).
  - O hash do código do compilador (`CODE_VERSION`, todos os fontes Python sob `nds_forge/` e o módulo executor externo, quando houver).
  - O tamanho e conteúdo de arquivos externos de entrada (`_external_input_fingerprints`).
  - Os hashes dos manifestos das etapas predecessoras imediatas no DAG.
- Qualquer alteração nos bytes de entrada ou nos parâmetros de configuração invalida o cache, forçando a reexecução.

### 1.2 Interrupção sem Publicação Parcial
- Cada etapa executa em um diretório temporário isolado (`.staging/<step-id>-<uuid>`).
- Exceções capturadas removem o staging da tentativa e registram falha em `state/<step_id com pontos substituídos por __>.failure.json`.
- A publicação troca diretórios com backup e restaura o anterior quando uma exceção capturada ocorre durante a troca. Os testes exercitam esse caso.
- SIGKILL, queda do sistema e falta de memória com encerramento abrupto não executam o cleanup Python nem garantem registro de falha. Podem deixar staging/backup residual; uma interrupção entre renames exige inspeção antes de remover resíduos. Não se afirma atomicidade durável contra crash.
- Ao reiniciar com `--resume` (`resume=True`), etapas precedentes válidas são reutilizadas via cache e a etapa interrompida recomeça de forma limpa.

---

## 2. Métricas de Recursos em Tempo de Execução

A execução agora instrumenta e persiste `resource_metrics` no manifesto de conclusão de cada etapa:

```json
{
  "duration_seconds": 1.245,
  "peak_rss_bytes": 48234496,
  "input_bytes": 1048576,
  "output_bytes": 524288
}
```

### 2.1 Normalização de Memória Multiplataforma
- No macOS (Darwin), `resource.getrusage(RUSAGE_SELF).ru_maxrss` retorna o valor em **bytes**.
- No Linux, `ru_maxrss` retorna o valor em **kilobytes**.
- A função `get_peak_rss_bytes()` normaliza ambas as plataformas para bytes absolutos inteiros, permitindo orçamentos determinísticos em CI e estações de desenvolvimento.

---

## 3. Benchmark Sintético do Clipping Preparado (Issue #59)

O benchmark reproduzível `tools/benchmark_osm_clip.py` gera um PBF sintético
local e executa cada clipping em um processo filho novo. A medição de RSS
exclui a geração do PBF, mas cobre o processo inteiro de clipping, incluindo
parse, construção do dataset e gravação dos outputs. O fixture contém 20.000
ways, 40.000 nós com tags de POI e um polígono circular de 1.024 vértices;
cada medição produziu 19.646 ways intersectantes, 39.292 nós coletados e
16.881.748 bytes de outputs. Não representa uma região real.

Ambiente medido: macOS arm64 (Darwin 25.5), Python 3.14.2, Shapely 2.2.0 e
pyosmium 4.3.2. O PBF gerado tinha 169.331 bytes e SHA-256
`4702f63ebc9ad864f952f16ee68fd5b764e4139521531251a569fcdee0c64efa`.
Três execuções em processos novos deram:

| Implementação | Mediana de tempo | Mediana de RSS máximo |
|---|---:|---:|
| anterior (`169c755`) | 3,150 s | 178.192.384 bytes |
| geometria preparada | 3,060 s | 176.472.064 bytes |

Nesse fixture, a diferença observada foi pequena (aproximadamente 2,9% em
tempo); não é evidência de ganho em dados estaduais e pode estar dentro da
variação do ambiente. O clipping preparado preservou os resultados: a hash de
`roads.geojson` foi `8587bf46f3da11804815ef298f42739dbcacde988d507dd032804274fb84ebfd`
em ambos, e o SHA-256 semântico do dataset foi
`06a68d4ad77f1b23cca3dcddb93f693a911e991397faeca6a2aa3f9faf071233` em ambos
(normalizado somente para remover o caminho temporário da origem).

Reprodução, usando o mesmo ambiente Python com as dependências `.[osm]`:

```sh
git worktree add --detach /tmp/nds-forge-clip-baseline 169c755
python tools/benchmark_osm_clip.py --ways 20000 --repeat 3 \
  --repo-root /tmp/nds-forge-clip-baseline
python tools/benchmark_osm_clip.py --ways 20000 --repeat 3
git worktree remove /tmp/nds-forge-clip-baseline
```

## 4. Linha de Base Regional (Curitiba v9)

Não há benchmark regional reproduzível vinculado a hashes de input/output e
ambiente neste documento. Os valores anteriormente listados (PBF ~25 MiB,
pacote <10 MiB e RSS <120 MiB) foram removidos por não estarem sustentados aqui.

Cada manifesto de execução contém os campos de recursos medidos pela execução
correspondente. `peak_rss_bytes` é o máximo histórico do processo Python, não um
pico isolado de cada etapa; não inclui automaticamente processos filhos. Para
estabelecer uma linha de base, arquive manifestos, hashes, versão do código,
ambiente e comando do build real. Testes sintéticos confirmam os campos e a
normalização, não os limites de Curitiba ou Paraná.

---

## 5. Diretriz Antitransposição Nacional

> [!IMPORTANT]
> **Proibição de Limites Nacionais Fictícios**:
> É estritamente vedado selecionar orçamentos de memória ou limites de tempo extrapolando linearmente a malha de Curitiba para todo o Brasil. Regiões metropolitanas complexas (como a macrometrópole de São Paulo) possuem ordens de magnitude mais nós e interconexões. Os orçamentos de processamento em lote devem ser calibrados a partir das medições empíricas de cada partição regional autorizada.
