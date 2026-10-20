# Contrato de Seleção e Pré-Visualização de POIs na UI (Issue #28 / [4.3])

Este documento descreve o contrato de consulta, seleção e renderização de POIs para a interface visual do sistema de navegação Neusoft, cumprindo os critérios da Issue [#28](https://github.com/Timoteohss/neusoft-nds-forge/issues/28) sob modo HITL.

---

## 1. Contrato de Seleção e Eco de Identificador

O módulo `nds_forge/validation/ui_contract.py` padroniza o fluxo de interação da UI veicular:

1. **Busca e Resolução**: O operador pesquisa por termo (`search_tags` ou `name`).
2. **Evento de Seleção (`UISelectionEvent`)**:
   - `item_type`: `"poi"`
   - `item_id`: Identificador canônico do POI (inteiro).
   - `display_name`: Nome formatado para exibição.
   - `category`: Categoria NDS padronizada.
   - `coord_lon`, `coord_lat`: Coordenadas WGS84 para enquadramento da câmera cartográfica.
   - `morton_code`: Código Morton 64-bit para indexação rápida no tile.
   - `candidate_hash`: Hash SHA-256 do pacote sob teste.
   - `target_zoom_level`: Nível 16 para pré-visualização detalhada.

---

## 2. Portão de Aceitação em Hardware (Gate 6.5)

Conforme os critérios de aceitação:
- A estabilidade da seleção mock e do payload JSON está comprovada em suite de testes automatizada (`tests/test_ui_contract.py`).
- O aceite de tela e renderização física na central multimídia do veículo é formalizado sob o **Portão de UI 6.5** com capturas de tela higienizadas (*scrubbed screenshots*).
- O cálculo de rota pelo SDK de forma isolada não substitui a validação de UI.
- A orientação por voz e manobras (*guidance*) é independente e tratada exclusivamente no portão 6.6.
