# Evidência e Pesquisa de Restrições de Conversão em NDS.Classic 2.4.4 (Issue #31 / [4.6])

Este documento consolida a pesquisa de representação de restrições de conversão (*turn restrictions*: nó de manobra / `via_node`, via intermediária / `via_way` e restrições condicionais) na arquitetura NDS e nos binários nativos do sistema de navegação, cumprindo os critérios da Issue [#31](https://github.com/Timoteohss/neusoft-nds-forge/issues/31) sob modo HITL.

---

## 1. Situação do Pipeline Legado e Limitações Conhecidas

O pipeline experimental de Curitiba documenta explicitamente a ausência de compilação de restrições nos manifestos de rota:

- Em `geely-ex2-investigation/neusoft-maps/tools/build_curitiba_full_route.py`:
  ```python
  'limitations': [
      'OSM turn-restriction relations are not compiled.',
      'Access conditions and guidance attributes are not compiled.'
  ]
  ```
- Em `geely-ex2-investigation/neusoft-maps/tools/build_curitiba_simplified_route.py`:
  ```python
  'limitations': [
      'OSM turn-restriction relations are not compiled.',
  ]
  ```
- Em `geely-ex2-investigation/neusoft-maps/tools/route_geometry.py`:
  > *"Partial NDS.Classic 2.4.4 routing codec, verified against Neusoft's reader. Header/intersection topology and inline basic-link attributes are covered. Conditional access, turn restrictions and guidance remain unresolved."*

Nenhum compilador legado gerou restrições de manobra em tiles binários finais.

---

## 2. API Nativa Inspecionada em `libNdsLib.so`

A inspeção dos símbolos e do desmonte de `libNdsLib.so` (compartilhada com `libna_dbu_sh.so` e `libna_onecore.so`) identifica as seguintes funções centrais do motor de roteamento:

| Símbolo Nativo | Papel Arquitetural NDS |
|---|---|
| `nds_rt_getIntersection` | Lê a topologia da interseção (links de entrada e saída). |
| `nds_rt_getIntersectionFlexibleAttributes` | Lê atributos flexíveis associados a manobras da interseção. |
| `nds_rt_getContinuedTurnRestrictions` | Desempacota restrições que continuam através de múltiplos links consecutivos (`via_way`). Opera sobre referências de links empacotadas em 64 bits (`(tile/region << 16) \| (link_offset << 1) \| heading`). |
| `hasCommonUTurnRestriction` | Avalia proibição de retorno na mesma via/interseção. |
| `nds_rt_getAttributeTimeConditions` | Avalia máscaras temporais (dias da semana, horários de pico, intervalos de validade). |
| `nds_rt_getGeoLineGuidanceAttributes` | Atributos de orientação e manobra para o motor de guidance. |

---

## 3. Representação Canônica e Níveis de Restrição

No modelo conceitual NDS.Classic e OSM:

### 3.1 Restrições Simples com Ponto de Manobra (`via_node`)
- **Proibições**: `no_left_turn`, `no_right_turn`, `no_u_turn`, `no_straight_on`.
  - Representadas como proibições explícitas de transição entre o link de entrada ($L_{in}$) e o link de saída ($L_{out}$) conectados ao nó de interseção $N_{via}$.
- **Obrigatoriedades**: `only_left_turn`, `only_right_turn`, `only_straight_on`.
  - No grafo de transição, proíbem todas as saídas da interseção para $L_{in}$ exceto $L_{out}$.

### 3.2 Restrições Complexas Multilink (`via_way`)
- Sequência de links: $L_{from} \rightarrow L_{via_1} \dots L_{via_k} \rightarrow L_{to}$.
- Exigem cadeia de links conectados (`nds_rt_getContinuedTurnRestrictions`). Se os links cruzarem fronteiras de tiles NDS de nível 13/15 sem gateways consistentes, a rota é invalidada.

### 3.3 Restrições Condicionais
- `restriction:conditional = no_left_turn @ (07:00-09:00,17:00-19:00; Mo-Fr)`
- Exigem descritor de tempo e calendário (`nds_rt_getAttributeTimeConditions`).

---

## 4. Matriz de Conhecidos vs. Desconhecidos (Unknowns Matrix)

| Categoria | Classificação | Evidência / Regra de Engenharia |
|---|---|---|
| Transições Proibidas em Grafo (`via_node`) | **Comprovado** | Transições direcionadas $(L_{in}, L_{out})$ no nó de interseção são modeláveis canonicamente sem ambiguidade. |
| Retorno Proibido (`no_u_turn`) | **Comprovado** | Transição $L_{in} \rightarrow L_{reverse}$ no mesmo segmento/nó é diretamente bloqueada. |
| Layout de Bits em `flexible_attributes` (Section 8) | **Desconhecido** | O layout binário exato dos bits proprietários de máscaras temporais do Neusoft para restrições horárias não possui especificação pública autorizada. **Não atribuir labels arbitrários a flags não identificados.** |
| Restrições Multilink além da fronteira do tile | **Bloqueado** | Sem gateways de rota cross-tile consolidados, cadeias `via_way` que cruzam múltiplos tiles não podem ser garantidas pelo compilador. |

---

## 5. Diretriz para a Implementação [4.7]

Para a Issue [#32](https://github.com/Timoteohss/neusoft-nds-forge/issues/32):
1. **Subconjunto Comprovado**:
   - Compilar restrições `via_node` em proibições de transição no grafo relacional de roteamento (`from_link_id`, `via_node_id`, `to_link_id`, `restriction_type`).
   - Converter restrições `only_*` na negação de todas as saídas concorrentes.
2. **Tratamento Rigoroso de Desconhecidos**:
   - Restrições condicionais (`restriction:conditional`) ou temporais não devem ser silenciosamente autorizadas como livres; devem emitir aviso de auditoria (`audit.unsupported_restrictions`) e, quando a política for restritiva/fail-closed, tratar a manobra como proibida ou bloquear a rota com aviso explícito.
   - Desconhecidos não devem ser assumidos como permitidos (*unknown is never silently allowed*).
