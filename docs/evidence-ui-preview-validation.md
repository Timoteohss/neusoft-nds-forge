# Validação de UI de Mapa, Busca e Prévia de Rota (Issue #40 / [6.5])

Este documento consolida a evidência física de validação em dispositivo (IHU629G) para renderização de mapa, pesquisa de POI e pré-visualização de rota no display veicular, cumprindo os critérios da Issue [#40](https://github.com/Timoteohss/neusoft-nds-forge/issues/40) sob modo HITL.

---

## 1. Identificação do Pacote e Integridade dos Hashes

- **Dispositivo**: IHU629G (Android Automotive / Neusoft Navigation R2).
- **Pacote Candidato**: `Geely_EX2_CURITIBA_v9_sparse_municipal` (103 tiles, 19.184 geometrias AUX).
- **Hash do Manifesto do Pacote Candidato**: `d5797800bfacb4719d463af4d9f725fd9e6e3ab23e105face26469c91ff9df42`.
- **Hash do `sdk.ini` Ativo**: `1f477714805bd35504a4bab4f06baae9b6fa0cc347e6982f76f3942f58987cfb`.
- **Hash do Rollback Durável no Dispositivo**: `0047dfdb78dce3191aab5d8a1dbeb37662c108e1bd34290fe08082f6074fa832`.
- **Captura Higienizada de UI (`ui-after-direction.png`)**: SHA-256 `a2e384be31fb670a5703d960b1617c09b8015feb9873a319aff87327cb1f569e`.

---

## 2. Resultados da Pré-Visualização na Interface do Veículo

1. **Busca Textual e Seleção**: A busca na interface por *"Catedral Basílica de Curitiba"* localizou com sucesso o POI gerado e permitiu sua seleção.
2. **Desenho de Rota de Prévia**:
   - O toque em **Direção** desenhou a rota calculada no display central com três alternativas de itinerário:
     - Recomendação: 8.420,87 m / 675 s.
     - Alternativo 1: 8.327,51 m / 685 s.
     - Alternativo 2: 8.410,86 m / 852 s.
   - O SDK retornou `SUCCESSFUL_LOCAL`.
   - O botão subsequente **Ir** não foi acionado.

---

## 3. Proveniência da Origem e Limite Inconclusivo

- A coordenada de origem reportada no callback da UI foi `(-25.456264097, -49.213493154)`.
- O último fix do Android GPS coletado cerca de 32 segundos depois registrou `(-25.455160, -49.220310)` com acurácia horizontal `hAcc = 7 m`, resultando em uma diferença espacial de aproximadamente **695 metros**.
- **Conclusão Formal**: Na ausência de captura simultânea com fix sincronizado e com o veículo estritamente estático, a proveniência automática da origem pela UI permanece **inconclusiva**. O sucesso do cálculo da prévia visual está validado, mas não deve ser interpretado como confirmação do origin tracking automático.

---

## 4. Ausência de Guidance

Conforme especificado no contrato do portão 6.5:
- Nenhuma orientação de voz ou manobra curva a curva (*turn-by-turn guidance*) foi ativada.
- O botão **Ir** não foi pressionado.
- Guidance é tratada de forma estritamente isolada sob o portão 6.6.
