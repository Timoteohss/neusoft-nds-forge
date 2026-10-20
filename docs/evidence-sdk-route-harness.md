# Suite de Cálculo de Rota do SDK (Issue #39 / [6.4])

Este documento descreve o arnês de testes de cálculo de rota com o SDK de Navegação Neusoft, cumprindo os critérios de aceitação da Issue [#39](https://github.com/Timoteohss/neusoft-nds-forge/issues/39) sob modo HITL.

---

## 1. Casos de Teste e Cenários Municipais de Curitiba

A suite (`nds_forge/validation/sdk_route.py`) cobre três cenários fundamentais de verificação:

1. **Cálculo no Mesmo Tile (`is_same_tile=True`)**:
   - Trajeto curto no centro urbano (ex: Praça Tiradentes $\rightarrow$ Praça Santos Andrade).
   - Valida topologia interna e resolução de links sem cruzamento de fronteiras de tile.
2. **Cálculo Cross-Tile (`is_cross_tile=True`)**:
   - Trajeto municipal de média extensão (ex: Centro $\rightarrow$ CIC / Santa Felicidade).
   - Valida a transição através de gateways entre múltiplos tiles NDS de nível 13.
3. **Ponto de Partida com GPS Fresco**:
   - Fix de GPS válido com carimbo UTC e acurácia submétrica/métrica ($< 10$ m).

---

## 2. Controles de Entrada, Callback e Eco de Parâmetros

Cada requisição submetida ao SDK exige:
- `candidate_hash`: Hash SHA-256 do pacote candidato compilado.
- `start_gps`: Coordenadas de partida com timestamp e acurácia.
- `destination`: Coordenadas e identificação do destino público.
- `guidance_enabled`: **Estritamente `False`**. O cálculo de rota sob o portão 6.4 não ativa orientação nem áudio de manobras (orientação por voz é tratada exclusivamente no portão 6.6).

O retorno do callback SDK valida:
- Status `SUCCESSFUL_LOCAL`.
- Distância métrica (`distance_meters`) e tempo estimado (`estimated_seconds`).
- Eco de parâmetros verificado (preferência de rota, coordenadas e hash do candidato).

---

## 3. Delimitação Regional e Fail-Closed

O pacote Curitiba v9 possui escopo estritamente municipal. Coordenadas fora da malha metropolitana de Curitiba retornam imediatamente falha por cobertura (`FAILED_DESTINATION_OUT_OF_COVERAGE`), impedindo tentativas de roteamento com tiles ausentes.
