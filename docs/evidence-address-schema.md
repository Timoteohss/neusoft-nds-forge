# Evidência e Pesquisa de Schema de Endereços e Caminho Nativo de Consulta (Issue #29 / [4.4])

Este documento consolida a pesquisa de schema, bitwise packing e caminho de consulta nativo para numeração predial e faixas de endereço (`house numbers` e `address ranges`), cobrindo os requisitos de aceitação da Issue [#29](https://github.com/Timoteohss/neusoft-nds-forge/issues/29) sob modo HITL.

---

## 1. Contexto e Limite do Pipeline Legado

No pipeline legado de compilação (`geely-ex2-investigation/neusoft-maps/tools/build_curitiba_package.py`, linhas 170–172), as tabelas de numeração predial são explicitamente esvaziadas e nunca reconstruídas:

```python
db.execute('DELETE FROM namedObject2HouseNRTable')
db.execute('DELETE FROM houseNR2NamedObjectTable')
db.execute('DELETE FROM ftsHouseNumberRangeTable')
```

Portanto, o construtor legado de Curitiba descarta completamente endereços prediais, delegando busca exclusivamente a logradouros (ruas / `namedObjectClass = 11`) e POIs.

---

## 2. Schema Verificado em `FTS.NDS`

A inspeção do banco NDS clássico (`FTS.NDS` de referência no bootstrap autorizado) revela três tabelas relacionais dedicadas à numeração predial:

### 2.1 `ftsHouseNumberRangeTable`
Define os intervalos ou pontos de endereçamento:

```sql
CREATE TABLE ftsHouseNumberRangeTable(
    rangeId INTEGER NOT NULL,
    type INTEGER NOT NULL,
    minHnr INTEGER,
    maxHnr INTEGER,
    languageCode INTEGER,
    prefix TEXT,
    suffix TEXT,
    addressPoint TEXT,
    PRIMARY KEY(rangeId),
    CHECK(minHnr IS NOT NULL OR addressPoint IS NOT NULL)
);
```

**Semântica dos Tipos (`type`):**
- `0`: Numeração contínua / mista (ambos pares e ímpares) ou número único discreto quando `minHnr == maxHnr`.
- `1`: Numeração estritamente par (`minHnr % 2 == 0` e `maxHnr % 2 == 0`).
- `2`: Numeração estritamente ímpar (`minHnr % 2 == 1` e `maxHnr % 2 == 1`).
- `3`: Ponto de endereço com string explícita (`addressPoint = ?`), por exemplo letras, números com bloco ou identificadores alfanuméricos.

### 2.2 `namedObject2HouseNRTable`
Mapeia logradouros (`namedObjectId`) para faixas de número (`rangeId`):

```sql
CREATE TABLE namedObject2HouseNRTable(
    namedObject2HouseNumberRangeId INTEGER NOT NULL,
    updateRegionId INTEGER NOT NULL,
    PRIMARY KEY(namedObject2HouseNumberRangeId, updateRegionId)
);
```

**Packing Bitwise Comprovado:**
A chave primária de 64 bits empacota o ID do logradouro nos 32 bits superiores e o ID do range nos 32 bits inferiores:
$$\text{namedObject2HouseNumberRangeId} = (\text{namedObjectId} \ll 32) \mid \text{rangeId}$$

### 2.3 `houseNR2NamedObjectTable`
Índice reverso mapeando faixas de número (`rangeId`) para logradouros (`namedObjectId`):

```sql
CREATE TABLE houseNR2NamedObjectTable(
    houseNumberRange2NamedObjectId INTEGER NOT NULL,
    updateRegionId INTEGER NOT NULL,
    PRIMARY KEY(houseNumberRange2NamedObjectId, updateRegionId)
);
```

**Packing Bitwise Comprovado:**
$$\text{houseNumberRange2NamedObjectId} = (\text{rangeId} \ll 32) \mid \text{namedObjectId}$$

---

## 3. Caminho Nativo de Execução e Queries Verificadas

A biblioteca nativa do motor NDS (`libNdsLib.so`, exportada também para `libna_dbu_sh.so` e `libna_onecore.so`) implementa o resolvedor nativo `nds_fts_getHouseNumberNamedObjIds`. As queries SQL compiladas no binário nativo foram extraídas diretamente:

### 3.1 Consulta de Faixa Predial por Número e Logradouro
```sql
SELECT hnr.rangeId 
FROM ftsHouseNumberRangeTable hnr 
WHERE ((hnr.minHnr <= ? AND hnr.maxHnr >= ? AND (hnr.type = ? OR hnr.type = 0)
 AND (hnr.prefix is ? AND hnr.suffix is ?))
 OR (hnr.type = 3 AND hnr.addressPoint = ?)) 
AND hnr.languageCode = ?
AND hnr.rangeId IN ( 
    SELECT no2hnrrange.namedObject2HouseNumberRangeId & 4294967295 
    FROM namedObject2HouseNRTable no2hnrrange 
    WHERE ((no2hnrrange.namedObject2HouseNumberRangeId >= (? << 32)) 
      AND (no2hnrrange.namedObject2HouseNumberRangeId < ((? + 1) << 32)))
);
```

### 3.2 Consulta Reversa de Logradouro por Faixa
```sql
SELECT hnr2no.houseNumberRange2NamedObjectId & 4294967295 
FROM houseNR2NamedObjectTable hnr2no 
WHERE hnr2no.houseNumberRange2NamedObjectId >= (? << 32) 
  AND hnr2no.houseNumberRange2NamedObjectId < ((? + 1) << 32);
```

### 3.3 Tratamento de Erros no Binário Nativo
Strings literais no binário:
- `getHouseNumberNamedObjIds: houseNumberQuery not prepared`
- `getHouseNumberNamedObjIds: house number empty`
- `getHouseNumberNamedObjIds: house number %s could not be converted to int`
- `getHouseNumberNamedObjIds: namedObjectIdQuery not prepared`

### 3.4 Interface Android / Thrift RPC
No aplicativo Java Android (`com/neusoft/nmasdk/search/thrift/AdressInfo.java` e `NMASearchThirftListenerImpl.java`):
- O serviço Thrift trafega campos `housenumber: List<String>`.
- O callback `onLocationSearchResultUpdate` extrai `adressInfo.housenumber` e popula `addressInfo.houseNumber`.

---

## 4. Matriz de Conhecidos vs. Desconhecidos (Unknowns Matrix)

| Dimensão | Estado | Evidência / Referência |
|---|---|---|
| Schema relacional FTS | **Conhecido e Verificado** | Tabelas `ftsHouseNumberRangeTable`, `namedObject2HouseNRTable`, `houseNR2NamedObjectTable` inspecionadas e validadas em `FTS.NDS`. |
| Codificação de Chaves | **Conhecido e Verificado** | Packed 64-bit inteiros: `(namedObjectId << 32) \| rangeId` e vice-versa. |
| Tipos de Paridade | **Conhecido e Verificado** | 0 = contínuo/misto, 1 = par, 2 = ímpar, 3 = alfanumérico pontual. |
| Query Nativa de Busca | **Conhecido e Verificado** | Verificada diretamente em `libNdsLib.so` (`nds_fts_getHouseNumberNamedObjIds`). |
| Interpolacão espacial / Lado da Via | **Desconhecido / Bloqueado** | Não existe campo de lado de via (esquerda/direita) nem coordenadas de interpolação em `FTS.NDS`. A ligação com geometria ocorre via atributo binário no tile ROUTE (`nds_rt_getHouseNumbersOfLink`), não implementado nem documentado no pipeline legado. |
| Pipeline Legado | **Incompleto / Bloqueador** | `build_curitiba_package.py` zera as tabelas e não recompila nenhum endereço predial. |

---

## 5. Diretriz para a Etapa 4.5 e Lançamento

Conforme critérios de aceitação:
1. **Não inventar encoder arbitrário**: A etapa [4.5] deve implementar apenas o subconjunto verificado em contrato (`ftsHouseNumberRangeTable` + mapeamento bitwise em `namedObject2HouseNRTable` / `houseNR2NamedObjectTable`).
2. **Formas não suportadas reportam Unknown**: Sem a compilação do link routing de `nds_rt_getHouseNumbersOfLink`, não é permitido alegar interpolação métrica contínua ao longo do link para cálculo de rota em produção.
3. **Bloqueador de Release**: O desconhecido da interpolação de roteamento deve permanecer sinalizado como limitação contratual até a validação UI/Hardware sob o portão 6.5.
