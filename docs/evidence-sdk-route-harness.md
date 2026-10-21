# Contrato de cálculo de rota SDK — integração pendente

O módulo `nds_forge/validation/sdk_route.py` define estruturas de requisição e
resultado. Ele ainda **não carrega o pacote candidato nem invoca o SDK Neusoft**.
O portão 6.4 da Issue #39 permanece pendente; testes unitários deste módulo não
validam links, gateways, topologia, callbacks, GPS fresco ou uma rota real.

A implementação anterior devolvia `SUCCESSFUL_LOCAL` usando distância em linha
reta multiplicada por 1,35. Esse resultado era sintético e não constitui evidência
de aceitação pelo SDK. O cálculo foi removido: sem adaptador real, qualquer
requisição retorna `UNSUPPORTED_SDK_ADAPTER`, distância/tempo zero e indicadores
de transição de tile falsos. O eco de parâmetros serve apenas ao contrato local.

Para concluir a integração, é necessário carregar um candidato identificado por
hash no runtime autorizado, capturar callbacks reais e seu eco de parâmetros,
verificar origem GPS sincronizada e testar rotas no mesmo tile e entre tiles.
Cobertura deve vir do candidato carregado, não de um bbox fixo. A orientação
continua separada do cálculo; `guidance_enabled=True` é rejeitado neste contrato.

A evidência histórica de prévia visual descrita em
[evidence-ui-preview-validation.md](evidence-ui-preview-validation.md) pertence
ao candidato e hashes ali registrados. Ela não valida automaticamente um novo
pacote produzido pelo forge, nem transforma este contrato em um adaptador SDK.
