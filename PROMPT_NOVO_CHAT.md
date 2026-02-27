# Prompt Para Novo Chat (Manutencao Pos-Implantacao)

Voce vai continuar a manutencao do projeto **midia-faren** no workspace atual.

## Leitura obrigatoria antes de codar
1. `README.md`
2. `MIGRATION_TRANSCRIBE.md`
3. `TODO.md`

So depois disso comece a implementar.

## Estado atual (nao regredir)
- Backends: `whisper_cpp`, `faster_whisper` (default), `whisperx`.
- Chunking com overlap + merge temporal ativo.
- Cancelamento real de job com tratamento limpo de cancelado.
- Guardrails anti-loop e anti-timestamp fora da faixa.
- Auto-recuperacao de transcricao com incidente + retry seguro.
- Glossario com dois modos:
  - `origem => destino` (correcao)
  - `termo` (contexto conhecido sem substituicao obrigatoria)
- Termos de contexto enviados para modelo em:
  - `faster_whisper` (`initial_prompt`)
  - `whisperx` (`initial_prompt`)
- UI reorganizada e padrao visual unificado no projeto.

## Meta principal daqui pra frente
Evoluir qualidade/estabilidade sem quebrar fluxo existente (Flask, rotas, SSE, fila, UI e compatibilidade de imports).

## Limites e foco
- Nao reescrever o projeto.
- Nao alterar download/conversao fora do necessario.
- Preservar compatibilidade com:
  - `mfaren/transcriber.py`
  - `mfaren/transcribe_service.py`

## Checklist tecnico obrigatorio
- Validar compilacao Python.
- Validar sintaxe JS alterado.
- Rodar testes existentes (`unittest`).
- Registrar no resumo final:
  - arquivos alterados
  - testes rodados
  - pendencias reais

## Criterios de aceite
- Fluxo de transcricao longo com progresso por chunks.
- Cancelar job interrompe de fato.
- Sem loop infinito repetitivo.
- TXT/SRT finais coerentes.
- Configuracoes persistem em `/api/settings`.
- UI continua funcional nas 3 telas: `/`, `/convert`, `/transcribe`.

Execute com rigor tecnico e foco em manter o que ja esta funcionando.
