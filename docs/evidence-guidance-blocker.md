# Requisitos de Manobras e Bloqueador Documentado de Orientação Turn-by-Turn (Issue #41 / [6.6])

Este documento formaliza o levantamento de requisitos de manobra e registra o bloqueador comprovado para navegação ativa curva a curva (*turn-by-turn guidance*), cumprindo os critérios da Issue [#41](https://github.com/Timoteohss/neusoft-nds-forge/issues/41) sob modo HITL.

---

## 1. Requisitos de Dados e Caminho de Callback de Manobras

A inspeção dos binários nativos (`libNdsLib.so`, `libna_onecore.so`) e da interface do SDK confirma que o cálculo de rota (`route calculation`) é desacoplado do motor de manobras e orientação (*guidance engine*):

1. **Atributos de Orientação em Tiles ROUTE**:
   - `nds_rt_getGeoLineGuidanceAttributes`
   - `nds_rt_getAllGuidanceAttributes`
   - Tabelas de signpost / placas (`nds_rt_getGeoLineSignposts`)
   - Faixas de tráfego (`nds_rt_getLaneInformationList`)
2. **Callbacks do Motor de Orientação**:
   - `onManeuverUpdated`: emite o próximo tipo de manobra (ícone, distância restante, nome da próxima via).
   - `onVoicePrompt`: gera eventos para o sintetizador de áudio/TTS.
   - Requer tracking de GPS em movimento sincronizado com o mapa (*map matching* contínuo).

---

## 2. Registro de Bloqueador Documentado (Acceptance Blocker)

Conforme os critérios de aceitação:
> *"either supported data path and observable guidance pass, or documented blocker; guidance stays unvalidated until observed. Current guidance was never started."*

### Fatos Comprovados no Veículo IHU629G:
1. **O botão "Ir" nunca foi pressionado**: Nos testes reais documentados em `route-validation-summary.md`, a validação parou na tela de prévia de rota com alternativas desenhadas.
2. **Guidance nunca foi iniciado**: Nenhuma instrução de voz, áudio ou manobra curva a curva foi executada ou observada em trânsito.
3. **Restrições de Conversão e Atributos de Manobra**: Como comprovado nas Issues #31 e #32, os compiladores experimentais legados não geravam atributos avançados de guidance em tiles ROUTE binários.
4. **Decisão Contratual**: A orientação ativa (*turn-by-turn guidance*) permanece **não validada** (*unvalidated*) até que ensaios em pista fechada sob condições aprovadas observem callbacks contínuos de manobra em veículo em movimento.
