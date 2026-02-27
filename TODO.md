# TODO - Implantacao de Transcricao Robusta (Flask + Faster-Whisper/WhisperX)

## Status de execucao (2026-02-14)
- [x] Fase 0 - Baseline e seguranca
- [x] Fase 1 - Contrato unico de backend
- [x] Fase 2 - Chunking robusto
- [x] Fase 3 - Backend faster-whisper
- [x] Fase 4 - Backend WhisperX (com fallback)
- [x] Fase 5 - UI e configuracoes
- [x] Fase 6 - Qualidade e observabilidade
- [x] Fase 7 - Testes e validacao

Observacoes:
- Auto-recuperacao adicionada apos as fases (analise + retry automatico).
- Glossario evoluido para correcao explicita e termos de contexto.
- UI reorganizada para padrao visual unico no projeto inteiro.


## Objetivo
Substituir o motor de transcricao atual por um pipeline robusto com `faster-whisper` + `chunking` (obrigatorio) e `WhisperX` (opcional), sem quebrar o app Flask existente.

## Escopo Obrigatorio
- Manter o Flask, rotas, SSE, banco e cards de status atuais.
- Manter a fila e o controle de jobs em `mfaren/jobs.py`.
- Integrar backend novo dentro de `mfaren/transcribe_*`.
- Implementar chunking com overlap e merge temporal consistente.
- Preservar os botoes e operacoes atuais (cancelar/parar/remover/refazer/ok).

## Fora de Escopo (nao fazer agora)
- Reescrever frontend completo.
- Trocar banco de dados.
- Alterar fluxo de download/conversao fora da transcricao.

## Regras Tecnicas
- Evitar arquivos gigantes; dividir por responsabilidade.
- Nao quebrar imports de compatibilidade (`mfaren/transcriber.py`, `mfaren/transcribe_service.py`).
- Logar inicio/fim de subprocesso/backend com PID/tempo.
- Erros devem chegar na UI com mensagem clara.

## Plano de Implantacao

### Fase 0 - Baseline e seguranca
- Criar flag/config `transcribe_backend` com valores: `whisper_cpp`, `faster_whisper`, `whisperx`.
- Default inicial: `faster_whisper`.
- Manter fallback automatico para `whisper_cpp` se dependencia faltar.
- Adicionar validacao de ambiente no startup da transcricao.

### Fase 1 - Contrato unico de backend
- Definir interface unica para backends:
  - entrada: wav/caminho, idioma, threads, beam, diarize, chunk config
  - saida: segmentos normalizados com `start/end/text/speaker?`
  - callbacks: progresso, heartbeat, cancelamento
- Aplicar contrato no pipeline atual sem mudar rotas Flask.

### Fase 2 - Chunking robusto (obrigatorio)
- Implementar chunking por tempo com overlap configuravel.
- Processar chunks em sequencia com checkpoint por chunk.
- Merge final com deduplicacao por janela de sobreposicao.
- Garantir ordem cronologica final e evitar repeticao na borda dos chunks.

### Fase 3 - Backend faster-whisper
- Integrar `faster-whisper` usando o contrato unico.
- Suportar CPU e CUDA.
- Mapear progresso por chunk + progresso total do arquivo.
- Reusar cache por hash e parametros.

### Fase 4 - Backend WhisperX (opcional, habilitavel)
- Integrar apenas se dependencias estiverem disponiveis.
- Usar para alinhamento/diarizacao quando solicitado.
- Se indisponivel, degradar com aviso para `faster-whisper`.

### Fase 5 - UI e configuracoes
- Expor seletor de backend na aba Transcricao.
- Expor parametros de chunking:
  - `chunk_seconds`
  - `chunk_overlap_seconds`
- Persistir em `/api/settings` imediatamente.

### Fase 6 - Qualidade e observabilidade
- Guardrails anti-loop:
  - deteccao de repeticao em janela
  - deteccao de timestamp fora da faixa
- Timeout por etapa + heartbeat continuo.
- Log final por arquivo: duracao, chunks, backend, tempo total, motivo de erro.

### Fase 7 - Testes e validacao
- Teste com arquivo curto, medio e longo.
- Teste com ZIP Craig multi-faixa e selecao parcial de internos.
- Teste de cancelamento durante transcricao.
- Teste de retomada/reuso de cache.

## Criterios de Aceite
- Nao travar indefinidamente em loop silencioso/repetitivo.
- Job cancelado deve parar processo de transcricao em segundos.
- Arquivos longos devem concluir com progresso real por chunk.
- SRT/TXT finais sem cauda repetitiva patologica.
- Flask e telas existentes funcionando sem regressao.

## Entregaveis
- Codigo integrado no projeto atual.
- Atualizacao de `README.md` com setup dos backends.
- Log de mudancas tecnico (arquivo novo `MIGRATION_TRANSCRIBE.md`).
- Checklist de validacao executado.

## Ordem recomendada de execucao
1. Fase 0
2. Fase 1
3. Fase 2
4. Fase 3
5. Fase 5
6. Fase 6
7. Fase 7
8. Fase 4 (se ambiente suportar)