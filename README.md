# Mídia Faren - Guia Completo de Instalação (Pacote ZIP / GitHub)

Este repositório foi preparado para rodar localmente no Windows **sem depender de Git**.

Você pode:
- baixar ZIP do projeto,
- descompactar,
- executar `executar.bat`.

---

## 1) O que este pacote contém

- `app.py`: servidor Flask da aplicação.
- `executar.bat`: instalador/validador automático + inicialização.
- `mfaren/`: backend principal (download, conversão, transcrição, filas, jobs).
- `static/` e `templates/`: frontend.
- `instalacao_de_resumo_de_sessao.html`: guia visual com fluxo de resumo no NotebookLM.
- `requirements.manual.txt`: referência de dependências para instalação manual.
- `tests/`: testes automatizados (opcional para quem só vai usar).

Estrutura de runtime incluída vazia:
- `data/uploads/`
- `data/transcribe_cache/`
- `downloads/`
- `logs/`

---

## 2) Requisitos obrigatórios

Instale estes itens antes da primeira execução:

1. Python 3.11+ (3.13 também funciona)
   - https://www.python.org/downloads/windows/
2. FFmpeg (build "full shared")
   - https://www.gyan.dev/ffmpeg/builds/
3. Microsoft Visual C++ Redistributable (x64)
   - https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist

Recomendado para melhor desempenho:

4. Driver NVIDIA atualizado (se usar GPU)
   - https://www.nvidia.com/Download/index.aspx
5. CUDA Toolkit (opcional para validação manual da toolchain)
   - https://developer.nvidia.com/cuda-downloads

Observações:
- Internet é necessária na primeira execução (download de pacotes Python/modelos).
- Reserve espaço livre em disco (ideal: 15 GB+ para operação confortável).

---

## 3) Instalação do zero (sem Git)

1. Baixe o ZIP do projeto.
2. Descompacte em uma pasta local (ex.: `C:\midia-faren`).
3. Abra a pasta e execute `executar.bat`.
4. Aguarde os passos de instalação/validação.
5. O navegador abrirá em: `http://127.0.0.1:5000/`.

Se o navegador não abrir automático, acesse manualmente a URL acima.

---

## 4) O que o `executar.bat` faz

- Cria/reutiliza o venv local: `.venv_cuda`
- Instala/atualiza pacotes base:
  - `flask`, `requests`, `yt-dlp`
- Instala stack IA fixada (estável para este projeto):
  - `torch==2.8.0+cu128`
  - `torchaudio==2.8.0+cu128`
  - `faster-whisper==1.2.1`
  - `whisperx==3.8.0`
  - `ctranslate2==4.7.1`
- Remove `torchcodec` por instabilidade no Windows nesse stack.
- Valida imports e sobe o Flask.

### Flags úteis do `executar.bat`

- `--check`: só valida ambiente, sem reinstalar.
- `--setup-env`: força reinstalação das dependências.
- `--recreate-venv`: recria o venv do zero.
- `--ffmpeg-path-on`: injeta FFmpeg local no PATH da sessão.
- `--ffmpeg-path-auto`: injeta só se não houver FFmpeg no PATH.
- `--ffmpeg-path-off`: não injeta PATH (padrão).

Exemplo:

```bat
executar.bat --setup-env --recreate-venv --ffmpeg-path-auto
```

---

## 5) FFmpeg: como garantir detecção

A aplicação procura FFmpeg nesta ordem:

1. `tools\ffmpeg\bin\ffmpeg.exe`
2. `C:\tools\ffmpeg\ffmpeg-7.1.1-full_build-shared\bin\ffmpeg.exe`
3. `ffmpeg` no PATH

Para não ter erro:
- opção A: instalar FFmpeg e adicionar no PATH;
- opção B: colocar binário em `tools\ffmpeg\bin` dentro do projeto;
- opção C: usar `executar.bat --ffmpeg-path-auto`.

Teste rápido:

```bat
ffmpeg -version
```

---

## 6) GPU vs CPU (comportamento real)

- O projeto roda com e sem CUDA.
- Com CUDA disponível: transcrição muito mais rápida.
- Sem CUDA: funciona em modo CPU (mais lento).

Verificação rápida:

```bat
.venv_cuda\Scripts\python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
```

---

## 7) Backends de transcrição

Disponíveis na UI:
- `whisper_cpp`
- `faster_whisper` (recomendado padrão)
- `whisperx`

Recursos do pipeline:
- chunking com overlap
- checkpoint por chunk
- retomada/reprocessamento de chunk
- guardrails anti-loop
- glossário/contexto persistente

---

## 8) Modelos (qualidade x velocidade)

- `tiny`: muito rápido, qualidade baixa.
- `base`: rápido, qualidade baixa/média.
- `small`: equilíbrio para teste.
- `medium`: qualidade boa com prazo.
- `large-v3`: melhor qualidade, mais lento.
- `distil-large-v3`: quase qualidade do large-v3 com mais velocidade.

Links oficiais dos modelos:
- https://huggingface.co/openai/whisper-tiny
- https://huggingface.co/openai/whisper-base
- https://huggingface.co/openai/whisper-small
- https://huggingface.co/openai/whisper-medium
- https://huggingface.co/openai/whisper-large-v3
- https://huggingface.co/distil-whisper/distil-large-v3

---

## 9) Importante sobre `whisper_cpp` neste pacote GitHub

Para manter o repositório leve e compatível com limites do GitHub:
- binários grandes e modelos `.bin` do `whisper_cpp` **não estão incluídos** neste pacote.

Impacto:
- `faster_whisper` e `whisperx` continuam prontos para uso;
- `whisper_cpp` pode exigir baixar binários/modelos separadamente se você quiser usar esse backend.

Se não quiser setup adicional, use:
- backend `faster_whisper` ou `whisperx`.

---

## 10) Fluxo recomendado: Craig -> Mixagem -> NotebookLM

1. Abra aba **Conversor**.
2. Selecione perfil **Mixagem**.
3. Envie ZIP/FLAC do Craig.
4. Gere arquivo final em M4A.
5. Acesse NotebookLM: https://notebooklm.google.com/
6. Crie notebook e envie o M4A.
7. Gere resumo de áudio.

Guia visual e prompt:
- `instalacao_de_resumo_de_sessao.html`

---

## 11) Verificações pós-instalação

```bat
.venv_cuda\Scripts\python -c "import flask,requests,yt_dlp; print('core_ok')"
.venv_cuda\Scripts\python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.version.cuda)"
.venv_cuda\Scripts\python -c "import importlib.metadata as md; print(md.version('whisperx'), md.version('faster-whisper'), md.version('ctranslate2'))"
```

---

## 12) Troubleshooting rápido

### `ffmpeg` não encontrado
- Reinstale FFmpeg full shared.
- Valide `ffmpeg -version`.
- Use `executar.bat --ffmpeg-path-auto`.

### Erro de dependência Python
- Rode:

```bat
executar.bat --setup-env --recreate-venv
```

### Transcrição lenta
- Use modelo menor (`small`/`medium`).
- Use backend `faster_whisper`.
- Verifique se CUDA está ativo.

### Loop/repetição no final
- Ajuste chunk/overlap.
- Revise glossário e contexto.
- Use reprocessamento de chunk.

---

## 13) Publicação no GitHub

Esta pasta já está preparada para publicação:
- `.gitignore` configurado para ignorar dados locais/caches.
- sem caminhos pessoais hardcoded.
- sem venv embutido.
- sem binários gigantes de `whisper_cpp`.

---

## 14) Créditos

Desenvolvido por **Paulo "Faren" Lima** - Artifício RPG
- https://artificiorpg.com/
