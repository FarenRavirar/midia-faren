# MIGRATION_TRANSCRIBE

## Decisoes tecnicas
- Mantida compatibilidade de facade:
  - `mfaren/transcriber.py`
  - `mfaren/transcribe_service.py`
- Introduzido contrato unico de backend em `mfaren/transcribe_backends.py` com 3 opcoes:
  - `whisper_cpp`
  - `faster_whisper` (default)
  - `whisperx`
- Fallback automatico de backend:
  - `faster_whisper` -> `whisper_cpp`
  - `whisperx` -> `faster_whisper` -> `whisper_cpp`
- Chunking robusto separado em `mfaren/transcribe_chunking.py`:
  - particionamento por tempo com overlap configuravel
  - merge cronologico com deduplicacao na borda de overlap
  - limpeza anti-loop com reuso dos filtros de `transcribe_postprocess`
- Pipeline de transcricao consolidado em chunking por backend:
  - progresso por chunk e progresso total de arquivo
  - estimativa de ETA por transcricao (baseada em audio processado x tempo real decorrido)
  - cache de transcricao por hash+parametros (backend/model/chunk/config)
  - guardrails para repeticao e timestamp fora da faixa
  - checkpoint persistente por chunk (`pending/done/failed`) com segmentos serializados
  - suporte a refazer somente chunk N e remontar SRT final a partir dos chunks `done`
- UI e settings:
  - novos campos `transcribe_backend`, `chunk_seconds`, `chunk_overlap_seconds`
  - `chunk_seconds` padrao em 5 minutos (300s), com input UI em formato `mm:ss`
  - editor de glossario por linhas (`transcribe_glossary`) com persistencia imediata
  - persistencia imediata via `/api/settings`
  - `compare_all` restrito a `whisper_cpp` (modelos `.bin`)
- Auto-recuperacao de transcricao:
  - analise de loop/timestamp/timeout por `live/srt/txt/log`
  - geracao de incidente json em `incidentes_transcricao/`
  - retry automatico com patch seguro de parametros (`redo_from=transcribe`)
- Glossario em dois modos:
  - `origem => destino` (substituicao explicita)
  - `termo` (contexto conhecido para o modelo, sem substituicao obrigatoria)
- Context prompt de termos conhecidos:
  - `faster_whisper`: `initial_prompt`
  - `whisperx`: `initial_prompt` (com fallback se assinatura nao suportar)
  - `whisper_cpp`: nao forcado por compatibilidade de CLI
- Cancelamento:
  - cancelamento intencional nao gera mais log de erro de job
  - loga `stage_cancel`/`Job canceled` de forma limpa
- Frontend:
  - reorganizacao das telas para reduzir poluicao e manter padrao unico
  - glossario com fluxo `Adicionar` + `Editar` + autosave ao perder foco

## Como testar
1. Validacao esttica:
   - PowerShell: `$files = @('app.py') + (Get-ChildItem mfaren -Filter *.py | % { $_.FullName }); python -m py_compile $files`
   - JS: `node --check static/transcribe.js; node --check static/app.js; node --check static/convert.js`
2. Suite automatizada:
   - `python -m unittest discover -s tests`
3. Fluxo funcional minimo:
   - abrir `/transcribe`
   - selecionar backend `faster_whisper`
   - iniciar arquivo curto e confirmar:
     - progresso da etapa de transcricao com sufixo `chunk X/Y`
     - geracao de `.srt` e `.txt`
   - testar botao `Parar job` durante transcricao
   - repetir com backend `whisper_cpp` (se instalado)
   - opcional: testar `whisperx` (se instalado)
   - executar um arquivo longo, interromper no meio e confirmar checkpoint:
     - `data/uploads/<projeto>/transcricao/*_chunks.json`
   - usar botao/API `redo/chunk` com `chunk_index` e confirmar remonte final sem reprocessar todos os chunks
4. Persistencia:
   - alterar backend/chunk no formulario
   - recarregar pagina e confirmar que valores foram preservados
   - editar glossario e confirmar persistencia imediata em reload
5. Contexto de termos:
   - cadastrar linha sem seta (ex.: `Kovir`)
   - executar com `faster_whisper` ou `whisperx`
   - confirmar em log de etapa que transcricao usa contexto (sem forcar substituicao literal)
6. Auto-recuperacao:
   - simular falha recuperavel (loop/timestamp)
   - confirmar `transcribe_recovery_trigger` no log
   - confirmar arquivo `incidentes_transcricao/*.json`
   - confirmar reexecucao automatica ate limite configurado

## Instalacao manual (quando faltar dependencia)
- `faster-whisper`:
  - `python -m pip install faster-whisper`
- `whisperx`:
  - `python -m pip install whisperx`
  - pode exigir stack CUDA/Torch especifica; se falhar, manter fallback automatico
- `whisper_cpp`:
  - manter `whisper-cli.exe` + DLLs CUDA em:
    - `transcriber/whisper-cublas-12.4.0-bin-x64/Release`
    - ou `transcriber/whisper-cublas-11.8.0-bin-x64/Release`

## Riscos conhecidos
- `whisperx` depende fortemente de ambiente (Torch/CUDA); fallback cobre indisponibilidade.
- cancelamento em backends Python (`faster_whisper`/`whisperx`) depende do checkpoint entre segmentos/chunks (na pratica tende a parar rapido, mas pode variar conforme hardware/modelo).
- `compare_all` permanece acoplado a modelos `.bin` de `whisper_cpp`.
- termos de contexto aumentam chance de acerto, mas nao garantem substituicao; nomes raros ainda dependem da qualidade do audio.

## Resultado da migracao
- Implantacao concluida com criterios principais atendidos.
- Fluxo validado por compilacao + testes automatizados.
- Estado operacional atual documentado no `README.md`.
