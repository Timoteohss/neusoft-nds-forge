# Relatório de Viabilidade de Tiles e Perfis NDS (Issue #33 / [5.2])

Este documento consolida a análise de viabilidade, capacidades arquiteturais e limites de contagem por tile para perfis de compilação NDS.Classic 2.4.4, atendendo aos requisitos da Issue [#33](https://github.com/Timoteohss/neusoft-nds-forge/issues/33) sob modo HITL.

---

## 1. Limites Arquiteturais do Formato NDS Classic 2.4.4

Os buffers binários de routing e geometria possuem limitações intrínsecas ao empacotamento em inteiros de 16 e 8 bits:

| Dimensão por Tile | Limite NDS | Mecanismo de Falha / Overflow |
|---|---|---|
| **Contagem de Links** | 32.767 | Índices de links locais usam inteiros de 16 bits; exceder causa overflow de índice ou truncamento. |
| **Contagem de Interseções** | 32.767 | Índices de interseção locais usam 16 bits. |
| **Grau Máximo de Nós (Degree)** | 16 | O padrão de interseções aloca nibbles de 4 bits para conexões simples; interseções com mais de 16 saídas/entradas exigem desdobramento em nós virtuais. |
| **Ordinais de Tiles Externos** | 255 | A tabela de tiles vizinhos (section 9) endereça ordinais em 8 bits. Exceder 255 vizinhos externos corrompe gateways. |
| **Tamanho do Blob AUX** | 16 MiB | Deslocamentos de geometria nos ponteiros de cabeçalho. |

O módulo `nds_forge/feasibility.py` avalia previamente essas métricas a partir dos grafos locais e impede a execução do compilador caso qualquer limiar seja violado (`raise_on_overflow=True`).

---

## 2. Evidência Experimental: Nível 13 vs. Nível 14

### Perfil Nível 13 (`neusoft-curitiba-v9`)
- **Status de Evidência**: **Validado no SDK local Neusoft.**
- Os blobs de rota compilados no nível 13 (shift 4) foram parseados com sucesso pelo leitor nativo `libNdsLib.so` e aceitos na árvore de navegação com polilinhas compactadas.
- O particionamento em nível 13 para a malha de Curitiba resulta em contagens de links e interseções confortavelmente abaixo de 50% dos tetos máximos.

### Perfil Nível 14 (`candidate-national-v14`)
- **Status de Evidência**: **Não comprovado no SDK.**
- Embora o nível 14 reduza a extensão espacial dos tiles pela metade (facilitando contagens em áreas densas), não há evidência de aceitação no SDK Neusoft do veículo Geely EX2 para níveis diferentes de 13 e 15.
- O catálogo de Israel e os binários Neusoft usam níveis 9 (índice), 13 (payload) e 15 (exploratório). A promoção do nível 14 para produção é bloqueada por ausência de oráculo nativo.

---

## 3. Diretriz Antitransposição Nacional

> [!IMPORTANT]
> **Proibição de Inferência de Densidade Nacional**:
> Não é permitido projetar a densidade, volumetria ou consumo de memória de São Paulo, Rio de Janeiro ou de todo o território brasileiro a partir do recorte de Curitiba. A densidade viária das capitais varia em ordens de magnitude. Cada região metropolitana deve ser medida individualmente pelo avaliador de viabilidade antes da etapa de compilação.
