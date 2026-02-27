# Smoke Test - 10 Minutos (Mídia Faren)

Objetivo: validar rapidamente se a instalação está funcional para uso real.

Tempo total estimado: ~10 minutos.

## Pré-requisito

- Projeto já descompactado.
- Dependências instaladas via `executar.bat` pelo menos uma vez.
- Ter 1 arquivo de áudio curto local (60 a 180 segundos) para teste.

## Checklist (passo a passo)

### 1) Subida do app (2-4 min)

Ação:
- Executar `executar.bat`.

Esperado:
- Flask inicia sem erro fatal.
- URL disponível: `http://127.0.0.1:5000/`.

Falha comum:
- Erro de Python/venv/pacotes.

Correção rápida:
- `executar.bat --setup-env --recreate-venv`

---

### 2) Healthcheck da interface (30s)

Ação:
- Abrir navegador na URL local.
- Verificar abas: Download, Conversor, Transcrição.

Esperado:
- Página carrega sem quebrar layout.
- Botões e campos visíveis.

---

### 3) Conversão local rápida (1-2 min)

Ação:
- Ir em Conversor.
- Selecionar um arquivo de mídia curto.
- Converter para outro formato (ex.: mp3/m4a).

Esperado:
- Job finaliza com status OK.
- Arquivo convertido aparece na pasta de saída.

---

### 4) Transcrição curta (2-3 min)

Ação:
- Ir em Transcrição.
- Configurar backend `faster_whisper`.
- Modelo: `small` (teste rápido).
- Diarização: desativada.
- Processar áudio de 1-3 min.

Esperado:
- Job completa sem travar.
- Saídas `.txt` e `.srt` geradas.
- Texto coerente (sem cauda em loop evidente).

---

### 5) Teste de cancelamento (1 min)

Ação:
- Iniciar uma transcrição (ou conversão) e clicar em "Parar tarefa" / "Parar job".

Esperado:
- Job muda para cancelado rapidamente.
- Processo não continua consumindo indefinidamente.

---

### 6) Verificação de logs (1 min)

Ação:
- Abrir `logs/app.log`.

Esperado:
- Sem traceback crítico ativo no fim do arquivo.
- Mensagens de etapa normais (start/end) presentes.

---

## Critério de aprovação

A instalação passa no smoke test se:

- App sobe e UI abre.
- Conversão curta termina com sucesso.
- Transcrição curta gera `.txt` + `.srt`.
- Cancelamento funciona.
- Sem erro crítico persistente no log.

## Se falhar

1. Reexecutar ambiente:
   - `executar.bat --setup-env --recreate-venv`
2. Validar FFmpeg:
   - `ffmpeg -version`
3. Validar stack Python:
   - `.venv_cuda\Scripts\python -c "import flask,requests,yt_dlp; print('core_ok')"`
4. Validar GPU (opcional):
   - `.venv_cuda\Scripts\python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"`
