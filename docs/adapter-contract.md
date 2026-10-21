# Contrato para adicionar etapas

O DAG é extensível por registro de `Step` e callable. O engine não precisa de
um `if` por novo compilador; ele resolve declaração e executor pelo ID.

```python
from nds_forge.registry import EXPERIMENTAL, Step, StepContext, register_step

def execute_fixture(context: StepContext) -> dict:
    # Escrever somente em context.output_dir, o staging privado desta execução.
    # Receber dependências já concluídas por context.dependency_outputs.
    return {"fixture_only": True}

register_step(
    Step(
        id="fixture.synthetic",
        status=EXPERIMENTAL,
        dependencies=("source.acquire",),
        output="fixture-synthetic",
        description="Produzir artefato sintético, sem compilar mapa",
        inputs=("source.acquire/source.json",),
        outputs=("fixture.json",),
        capabilities=("synthetic_fixture",),
        external_inputs=("fixture.input_file",),
    ),
    execute_fixture,
)
```

`StepContext` contém:

- `config`: `ProjectConfig` validado;
- `output_dir`: diretório de staging exclusivo que o executor deve preencher;
- `dependency_outputs`: mapa de ID da dependência para o diretório de artefatos
  já publicados;
- `external_inputs`: paths resolvidos a partir dos campos TOML declarados em
  `Step.external_inputs`;
- `allow_network` e `allow_experimental`: gates explícitos recebidos pelo run.

O callable retorna metadata JSON serializável. O engine calcula hashes no
staging, constrói e grava o manifest de execução antes da publicação, mantém
`validation_status=unvalidated`, e publica o diretório em
`workspace/artifacts/<Step.output>` junto com o manifest de estado. Se a
publicação do manifest falhar, o diretório anterior é restaurado. O
executor não deve escrever fora do staging, alterar configuração de app/carro,
ler segredos sem secret-provider declarado, marcar saída validada ou acionar
rede/dispositivo sem capability e gate documentados.

`run` mantém um lock exclusivo em `workspace.lock` durante a execução. POSIX
usa `fcntl.flock`; Windows usa `msvcrt.locking`. O lock é não bloqueante com
timeout curto, e uma colisão falha antes de criar staging. O arquivo contém
somente PID e hostname para diagnóstico; o sistema operacional libera o lock
em sucesso, exceção ou encerramento do processo. O arquivo fica na raiz do
workspace e não faz parte dos hashes dos artefatos.

Hashes de fontes e arquivos de código são cacheados somente durante um `run`,
por caminho resolvido, tamanho e `mtime_ns`; o inventário de fontes Python é
descoberto uma vez. Mudanças de tamanho ou `mtime_ns` invalidam o digest. Uma
alteração concorrente que preserve ambos os metadados não pode ser detectada e
é comportamento indefinido; o cache nunca é reutilizado entre execuções.

`status` deve ser `implemented`, `experimental`, `planned` ou `blocked`. Um
step com `planned` ou `blocked` não pode ter execução parcial: o DAG inteiro é
preflightado antes de qualquer staging. `experimental` exige
`--allow-experimental`. `outputs`, `capabilities` e `capabilities_missing`
descrevem o que o step pretende gerar e as lacunas conhecidas; são afirmações
testáveis, não aprovação de produto.

Cada `external_inputs` aponta para um campo dotted do TOML cujo valor é um
arquivo ou diretório local. O engine exige que exista antes de criar staging e
calcula tamanho e SHA-256 (para diretórios, hash estável dos nomes relativos e
hashes dos arquivos) antes de decidir cache/resume. Alterar bytes no mesmo
caminho invalida o step e seus descendentes. Dados intermediários devem ser
consumidos por `dependency_outputs`; executores não devem esconder caminhos
externos no próprio código. Se um template, POI file ou grafo for externo,
declare um campo de config e `external_inputs`, ou crie uma etapa anterior de
aquisição/normalização.

O registry de callables é uma API Python, não um import automático de módulos
de configuração. Isso mantém `plan` sem importar código arbitrário nem
side-effects. Um consumidor que compõe extensão registra callables antes de
chamar `plan`, `run`, `resume` ou `status`. Futuro loader CLI poderá ser uma
etapa própria com módulo/hash/capabilities explícitos.

## Modelo canônico

`RegionSpec` e `StageContext` em `nds_forge.contracts` são os contratos comuns
de clipping e compiladores. `RegionSpec` carrega ID/nome, bbox WGS84, geometria
GeoJSON inline opcional e booleano `resolved`. Uma relação OSM sem polygon fica
`resolved=False` e não deve ser usada para recortar. Uma caixa tem `bbox` e
geometria ausente; não se inventa polígono detalhado a partir da extent.

O clipping experimental gera vias selecionadas, nós conectados, alguns POIs e
relações OSM brutas. A matriz de capabilities distingue esses dados retidos de
camadas compiladas: `restriction_relations_raw` pode ser produzida, mas
`turn_restrictions` permanece ausente até que regras semânticas sejam
compiladas e validadas para o grafo. O passo posterior `osm.normalize`
permanece `planned` enquanto não normalizar e tipar as entidades necessárias a
cada consumidor.
