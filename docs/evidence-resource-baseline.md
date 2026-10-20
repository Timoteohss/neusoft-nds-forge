# Linha de Base de Recursos e Execução Retomável (Issue #35 / [5.4])

Este documento descreve os mecanismos de medição de recursos, processamento em lotes com retomada atômica (*atomic resume*) e invalidação por fingerprints, cumprindo os critérios de aceitação da Issue [#35](https://github.com/Timoteohss/neusoft-nds-forge/issues/35) sob modo AFK.

---

## 1. Mecanismo de Retomada Atômica e Fingerprints

O orquestrador do pipeline (`nds_forge/pipeline.py`) garante isolamento e integridade nas seguintes condições:

### 1.1 Invalidação por Mudança de Entrada
- O `fingerprint` de cada etapa combina:
  - O hash canônico da configuração do projeto (`config.fingerprint_data()`).
  - O hash do código do compilador (`CODE_VERSION` e digest do módulo).
  - O tamanho e conteúdo de arquivos externos de entrada (`_external_input_fingerprints`).
  - Os hashes dos manifestos das etapas predecessoras imediatas no DAG.
- Qualquer alteração nos bytes de entrada ou nos parâmetros de configuração invalida o cache, forçando a reexecução.

### 1.2 Interrupção sem Publicação Parcial
- Cada etapa executa em um diretório temporário isolado (`.staging/<step-id>-<uuid>`).
- Se o processo for interrompido (exceção, SIGKILL, falta de memória), o diretório `.staging` é descartado.
- O diretório final em `artifacts/<output>` permanece intacto ou inexistente; **nenhum artefato parcial é publicado**.
- A falha é registrada em `.failures/<step_id>.json`.
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

## 3. Linha de Base Regional (Curitiba v9)

Medições observadas na execução do lote municipal de Curitiba:
- **Tempo de Execução**: de dezenas de milissegundos (resolução de limites, metadados) a poucos segundos (clip PBF, compilação de geometria e montagem do pacote).
- **Consumo de Memória (Peak RSS)**: < 120 MiB para compilação geométrica e empacotamento completo.
- **Volume de I/O**: PBF de entrada (~25 MiB), arquivos finais empacotados (< 10 MiB).

---

## 4. Diretriz Antitransposição Nacional

> [!IMPORTANT]
> **Proibição de Limites Nacionais Fictícios**:
> É estritamente vedado selecionar orçamentos de memória ou limites de tempo extrapolando linearmente a malha de Curitiba para todo o Brasil. Regiões metropolitanas complexas (como a macrometrópole de São Paulo) possuem ordens de magnitude mais nós e interconexões. Os orçamentos de processamento em lote devem ser calibrados a partir das medições empíricas de cada partição regional autorizada.
