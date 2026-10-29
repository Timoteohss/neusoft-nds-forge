# Busca de número por ROUTE: resultado de E0 (2026-10-26)

**Atualização posterior:** [achados do carro e parser](house-number-oem-progress-2026-10-26.md).
O carro revelou um consumidor nativo em segundo plano; o No-Go do AVD não é
conclusão geral. E1 avançou para 100% dos 3.879 blobs. O registro abaixo preserva
o resultado anterior do AVD. Trabalho parado por solicitação do usuário.

**NO_GO_E0 no AVD para a alteração isolada de camada proposta.** O teste não
justifica desenvolver um encoder de atributos como solução da busca atual.

Foram criadas duas cópias experimentais do pacote Curitiba. Cada uma recebeu
apenas um `nameAttributeLayer` OEM real, de 24.060 bytes, versão armazenada 1:
uma no tile do endereço OSM 71 (`664743901`) e outra no tile da coordenada
retornada pela busca da rua (`664743895`). Todos os tiles principais, a geometria
AUX e os demais arquivos permaneceram equivalentes.

O leitor nativo aceitou o blob. No segundo tile, os controles diretos retornaram
um registro de nome e um de número. O FTS de número retornou 1 para Luiz França
71 e 0 para 50. Os hooks capturaram essas chamadas e duas consultas SQL de número,
confirmando que a instrumentação observava o caminho esperado.

Porém, `addressSearch` OFFLINE e `searchSuggestion` continuaram sem chamar
`nds_rt_getHouseNumbersOfLink` ou `nds_fts_getHouseNumberNamedObjIds`, e sem SQL
das tabelas de número. A rua sem número retornou 1; com 71 ou 50, retornou 0.
Na UI, o campo foi verificado como `Rua Luiz Franca 71`; a confirmação mostrou
`No result`. A rede externa do UID do app estava bloqueada em IPv4/IPv6, mantendo
loopback para Thrift, e houve rejeições de tráfego externo registradas.

O resultado se limita ao SDK e aos fluxos executados no AVD. Não prova
impossibilidade para todo firmware ou pacote OEM completo: as referências do blob
não foram remapeadas para Curitiba, e outras flags/tabelas de capacidade podem
existir. Prova que a simples presença de camada aceita e legível não habilita
o consumidor no pacote atual. O carro não foi usado.

Conforme o critério de parada solicitado, **E1–E5 não foram executados**. Não há
parser com cobertura de 3.879 blobs, especificação fechada de attrIds/payloads,
encoder, roundtrip de atributos nem projeção ponto→link implementados nesta
tarefa. O inventário de 3.879 camadas não deve ser confundido com cobertura de
decodificação. As tabelas de número já produzidas pelo Forge continuam válidas.

Evidências locais, fora deste repositório por conterem dados proprietários:

```text
geely-ex2-investigation/neusoft-maps/native/house-number-20261026/
  oem-investigation/OEM-FINDINGS.md
  oem-investigation/e0-summary.json
  oem-investigation/runtime-e0-v7/
  oem-investigation/runtime-e0-centroid/
  oem-investigation/hook-selftest/
  oem-investigation/runtime-library-identities.json
```

Os hashes das três bibliotecas executadas coincidem com as bibliotecas locais;
`libNdsLib.so` é `c34a7e7a6201cb469bd6c9b2d62d36ef606f12a369ce3027969647a34a970e55`.
Nenhuma biblioteca nem APK foi alterado/instalado. `sdk.ini` e rede do AVD foram
restaurados, e o servidor Frida iniciado pelo teste foi parado.

Próximo passo recomendado: integrar o resolver ao serviço/callback que recebe
o texto, usando o índice de coordenadas do Forge e correlacionando respostas por
requestID. A injeção runtime já existente é a prova inicial dessa alternativa;
um patch nativo permanente permanece trabalho futuro. Retomar o encoder OEM
como solução de produto exige primeiro encontrar uma condição adicional de
dados/capacidade que faça o fluxo normal consultar números.
