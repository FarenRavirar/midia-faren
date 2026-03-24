@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d %~dp0

set "APP_URL=http://127.0.0.1:5000/"
set "CHECK_ONLY=0"
set "INSTALL_AI=0"
set "SETUP_ENV=0"
set "RECREATE_VENV=0"
set "FFMPEG_PATH_MODE=off"
set "ROOT_DIR=%CD%"
if not exist "C:\projetos\.venvs" mkdir "C:\projetos\.venvs"
set "VENV_DIR=C:\projetos\.venvs\cuda_shared"
set "PYTHON_BOOTSTRAP="
set "PYTHON_CMD="
set "FFMPEG_SHARED_BIN=%ROOT_DIR%\tools\ffmpeg\ffmpeg-7.1.1-full_build-shared\bin"
set "MFAREN_TMP_ROOT=D:\midia_temp"
set "MFAREN_DATA_ROOT=D:\midia-faren-data"
set "MFAREN_MIGRATION_STAMP=%MFAREN_DATA_ROOT%\.migrated_from_c.stamp"
set "HF_HOME=%MFAREN_DATA_ROOT%\model_cache"
set "TORCH_HOME=%MFAREN_DATA_ROOT%\torch_cache"

echo [INFO] Contingencia Anti-Travas: Varrendo processos orfaos que bloqueiam arquivos...
for /f "tokens=5" %%p in ('netstat -aon ^| findstr :5000 ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>&1
wmic process where "name='python.exe' and ExecutablePath like '%%cuda_shared%%'" call terminate >nul 2>&1

:parse_args
if "%~1"=="" goto after_parse_args
if /I "%~1"=="--check" set "CHECK_ONLY=1"
if /I "%~1"=="--install-ai" set "INSTALL_AI=1"
if /I "%~1"=="--setup-env" set "SETUP_ENV=1"
if /I "%~1"=="--recreate-venv" set "RECREATE_VENV=1"
if /I "%~1"=="--ffmpeg-path-on" set "FFMPEG_PATH_MODE=on"
if /I "%~1"=="--ffmpeg-path-off" set "FFMPEG_PATH_MODE=off"
if /I "%~1"=="--ffmpeg-path-auto" set "FFMPEG_PATH_MODE=auto"
shift
goto parse_args

:after_parse_args
if not exist logs mkdir logs
if not exist data mkdir data
if not exist downloads mkdir downloads

if exist "D:\" (
  if not exist "%MFAREN_TMP_ROOT%" mkdir "%MFAREN_TMP_ROOT%"
  if not exist "%MFAREN_DATA_ROOT%" mkdir "%MFAREN_DATA_ROOT%"
  if not exist "%MFAREN_DATA_ROOT%\uploads" mkdir "%MFAREN_DATA_ROOT%\uploads"
  if not exist "%MFAREN_DATA_ROOT%\transcribe_cache" mkdir "%MFAREN_DATA_ROOT%\transcribe_cache"
  set "TMP=%MFAREN_TMP_ROOT%"
  set "TEMP=%MFAREN_TMP_ROOT%"
  set "MFAREN_UPLOADS_DIR=%MFAREN_DATA_ROOT%\uploads"
  set "MFAREN_TRANSCRIBE_CACHE_DIR=%MFAREN_DATA_ROOT%\transcribe_cache"
  set "MFAREN_TRANSCRIBE_MANIFEST_PATH=%MFAREN_DATA_ROOT%\transcribe_manifest.json"
  echo [INFO] IO em D: TEMP/TMP=%MFAREN_TMP_ROOT%
  echo [INFO] IO em D: uploads=!MFAREN_UPLOADS_DIR!
  echo [INFO] IO em D: transcribe_cache=!MFAREN_TRANSCRIBE_CACHE_DIR!
  if not exist "%MFAREN_MIGRATION_STAMP%" (
    call :migrate_data_to_d
    if errorlevel 1 (
      echo [WARN] Migracao C:-^>D: incompleta. Tentarei novamente na proxima execucao.
    ) else (
      echo [INFO] Migracao C:-^>D: concluida.
    )
  )
) else (
  echo [WARN] Disco D: nao encontrado. Mantendo caminhos padrao no C:.
)

if exist "%VENV_DIR%\Scripts\python.exe" (
  set "PYTHON_BOOTSTRAP=%VENV_DIR%\Scripts\python.exe"
  echo [INFO] Bootstrap Python: venv existente
) else (
  call :pick_python
  if errorlevel 1 goto :fail
)

if "%RECREATE_VENV%"=="1" (
  if exist "%VENV_DIR%" (
    echo [INFO] Removendo venv atual: %VENV_DIR%
    rmdir /S /Q "%VENV_DIR%"
  )
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo [1/9] Criando venv local em %VENV_DIR%...
  %PYTHON_BOOTSTRAP% -m venv "%VENV_DIR%"
  if errorlevel 1 (
    echo [ERRO] Falha ao criar o venv local.
    goto :fail
  )
) else (
  echo [1/9] Venv local encontrado: %VENV_DIR%
)

set "PYTHON_CMD=%VENV_DIR%\Scripts\python.exe"

if /I "%FFMPEG_PATH_MODE%"=="off" (
  echo [2/9] PATH do FFmpeg compartilhado desativado - padrao.
) else (
  if exist "%FFMPEG_SHARED_BIN%\ffmpeg.exe" (
    if /I "%FFMPEG_PATH_MODE%"=="on" (
      set "PATH=%FFMPEG_SHARED_BIN%;!PATH!"
      echo [2/9] FFmpeg shared adicionado ao PATH da sessao - forcado.
    ) else (
      where ffmpeg >nul 2>&1
      if errorlevel 1 (
        set "PATH=%FFMPEG_SHARED_BIN%;!PATH!"
        echo [2/9] FFmpeg shared detectado e adicionado ao PATH da sessao.
      ) else (
        echo [2/9] FFmpeg ja encontrado no PATH atual. Sem injetar caminho compartilhado.
      )
    )
  ) else (
    echo [2/9] FFmpeg ausente. Auto-baixando build shared 7.1.1 para pasta tools...
    if not exist "%ROOT_DIR%\tools\ffmpeg" mkdir "%ROOT_DIR%\tools\ffmpeg"
    curl -# -L -o "%ROOT_DIR%\tools\ffmpeg\ffmpeg.zip" https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-full_build-shared.zip
    tar -xf "%ROOT_DIR%\tools\ffmpeg\ffmpeg.zip" -C "%ROOT_DIR%\tools\ffmpeg"
    del /Q "%ROOT_DIR%\tools\ffmpeg\ffmpeg.zip" >nul 2>&1
    set "PATH=%FFMPEG_SHARED_BIN%;!PATH!"
    echo [2/9] FFmpeg baixado e injetado via PATH local com sucesso.
  )
)

echo [3/9] Validando Python do venv...
"%PYTHON_CMD%" -c "import sys; print('python=', sys.executable); print('version=', sys.version)"
"%PYTHON_CMD%" -c "import sys; sys.exit(0 if sys.version_info < (3,14) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [ERRO] Python do venv ^(3.14 ou superior^) indisponivel para WhisperX.
  echo [INFO] Por favor, recrie o venv usando: executar.bat --setup-env --recreate-venv
  goto :fail_with_help
)

if "%CHECK_ONLY%"=="1" goto check_only

if "%INSTALL_AI%"=="1" set "SETUP_ENV=1"

if "%SETUP_ENV%"=="0" (
  call :check_ai_stack
  if errorlevel 1 (
    echo [INFO] Stack IA fora do padrao. Executando reparo automatico...
    set "SETUP_ENV=1"
  )
)

if "%SETUP_ENV%"=="1" (
  echo [INFO] Higienizando lixo de bloqueios - limpando pacotes corrompidos do PIP...
  call :clear_pip_locks

  echo [4/9] Atualizando ferramentas base de instalacao...
  "%PYTHON_CMD%" -m pip install --upgrade pip setuptools wheel
  if errorlevel 1 goto :fail

  echo [5/9] Instalando pacotes obrigatorios do app...
  "%PYTHON_CMD%" -m pip install --upgrade flask requests yt-dlp
  if errorlevel 1 goto :fail

  echo [6/9] Instalando stack CUDA fixa - PyTorch 2.9.1 + cu128...
  "%PYTHON_CMD%" -m pip install --upgrade torch==2.9.1+cu128 torchaudio==2.9.1+cu128 --index-url https://download.pytorch.org/whl/cu128
  if errorlevel 1 goto :fail

  echo [7/9] Instalando stack de transcricao fixa...
  "%PYTHON_CMD%" -m pip install --upgrade faster-whisper==1.2.1 whisperx==3.8.0 ctranslate2==4.7.1
  if errorlevel 1 goto :fail
  "%PYTHON_CMD%" -m pip uninstall -y torchcodec >nul 2>&1
  echo [INFO] torchcodec removido - instavel no Windows com este stack; nao necessario para este fluxo.
) else (
  echo [4/9] Ambiente IA ja esta no padrao esperado.
)

echo [8/9] Validando CUDA e stack de transcricao...
call :validate
if errorlevel 1 goto :fail_with_help

echo [9/9] Verificando FFmpeg via app...
"%PYTHON_CMD%" -c "from mfaren.ffmpeg import find_ffmpeg; import sys; sys.exit(0 if find_ffmpeg() else 1)" >nul 2>&1
if errorlevel 1 (
  echo [WARN] FFmpeg nao encontrado pelo app. Conversao/transcricao podem falhar.
)

echo [INFO] Checando conflitos de pacotes...
"%PYTHON_CMD%" -m pip check >nul 2>&1
if errorlevel 1 (
  echo [WARN] Conflitos detectados:
  "%PYTHON_CMD%" -m pip check
)

echo [INFO] Ambiente verificado e portas higienizadas.
echo Iniciando Flask...
set "MFAREN_DEBUG=0"
start "" "%APP_URL%"
"%PYTHON_CMD%" app.py
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo [ERRO] Flask finalizou com codigo %RC%.
  goto :fail_keep_rc
)
goto :ok

:check_only
echo [4/9] Modo --check: validando ambiente sem alterar instalacao...
call :validate
if errorlevel 1 goto :fail_with_help
echo [OK] Verificacao concluida.
goto :ok

:validate
"%PYTHON_CMD%" -c "import flask,requests,yt_dlp; print('core_ok')"
if errorlevel 1 exit /b 1
"%PYTHON_CMD%" -c "import torch; import sys; cuda_ok=torch.cuda.is_available(); print('torch',torch.__version__); print('cuda_available',cuda_ok); print('torch_cuda',torch.version.cuda); print('gpu_count',torch.cuda.device_count()); print('runtime_mode', 'gpu_cuda' if cuda_ok else 'cpu_only'); sys.exit(0)"
if errorlevel 1 exit /b 1
"%PYTHON_CMD%" -c "import importlib.metadata as md; print('whisperx',md.version('whisperx')); print('faster_whisper',md.version('faster-whisper')); print('ctranslate2',md.version('ctranslate2'))"
if errorlevel 1 exit /b 1
exit /b 0

:pick_python
py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_BOOTSTRAP=py -3.11"
  echo [INFO] Bootstrap Python: 3.11
  exit /b 0
)
py -3.13 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_BOOTSTRAP=py -3.13"
  echo [WARN] Python 3.11 nao encontrado. Usando 3.13.
  exit /b 0
)
python -c "import sys; sys.exit(0 if sys.version_info < (3,14) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_BOOTSTRAP=python"
  echo [WARN] py launcher indisponivel. Usando python do PATH.
  exit /b 0
)

echo [INFO] Python 3.11 ausente. Iniciando auto-instalacao nativa (sem necessidade de admin)...
set "PY_INSTALLER=%TEMP%\python-3.11.9-amd64.exe"
curl -# -L -o "%PY_INSTALLER%" https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
if not exist "%PY_INSTALLER%" (
  echo [ERRO] Falha ao efetuar download do Python.
  exit /b 1
)
echo [INFO] Instalando executaveis invisivelmente no AppData do usuario...
start /wait "" "%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
del /Q "%PY_INSTALLER%" >nul 2>&1

set "LOCAL_PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if exist "%LOCAL_PY%" (
  set "PYTHON_BOOTSTRAP=%LOCAL_PY%"
  echo [INFO] Python 3.11.9 instalado na maquina e injetado com sucesso!
  exit /b 0
)
echo [ERRO] Auto-instalacao do Python falhou. Baixe de: https://www.python.org/downloads/windows/
exit /b 1

:check_ai_stack
"%PYTHON_CMD%" -c "import sys,torch,torchaudio,importlib.metadata as md; ok=(torch.__version__=='2.9.1+cu128' and torchaudio.__version__=='2.9.1+cu128' and md.version('whisperx')=='3.8.0' and md.version('faster-whisper')=='1.2.1' and md.version('ctranslate2').startswith('4.7.1')); sys.exit(0 if ok else 1)" >nul 2>&1
exit /b %ERRORLEVEL%

:clear_pip_locks
for /d %%i in ("%VENV_DIR%\Lib\site-packages\~*") do rmdir /s /q "%%i" >nul 2>&1
exit /b 0

:migrate_data_to_d
set "MIG_FAIL=0"
set "SRC_UPLOADS=%ROOT_DIR%\data\uploads"
set "SRC_CACHE=%ROOT_DIR%\data\transcribe_cache"
set "SRC_MANIFEST=%ROOT_DIR%\data\transcribe_manifest.json"
echo [INFO] Iniciando migracao de dados para D: (uma vez)...

if exist "%SRC_UPLOADS%" (
  call :_robocopy_move "%SRC_UPLOADS%" "%MFAREN_UPLOADS_DIR%" uploads
  if errorlevel 1 set "MIG_FAIL=1"
)
if exist "%SRC_CACHE%" (
  call :_robocopy_move "%SRC_CACHE%" "%MFAREN_TRANSCRIBE_CACHE_DIR%" transcribe_cache
  if errorlevel 1 set "MIG_FAIL=1"
)
if exist "%SRC_MANIFEST%" (
  copy /Y "%SRC_MANIFEST%" "%MFAREN_TRANSCRIBE_MANIFEST_PATH%" >nul
  if errorlevel 1 (
    echo [WARN] Falha ao copiar transcribe_manifest.json para D:
    set "MIG_FAIL=1"
  ) else (
    del /F /Q "%SRC_MANIFEST%" >nul 2>&1
    echo [INFO] Migracao transcribe_manifest.json concluida.
  )
)

if "%MIG_FAIL%"=="0" (
  >"%MFAREN_MIGRATION_STAMP%" echo migrated=%date% %time%
  exit /b 0
)
exit /b 1

:_robocopy_move
setlocal
set "SRC=%~1"
set "DST=%~2"
set "LABEL=%~3"
if not exist "%SRC%" (
  endlocal & exit /b 0
)
if not exist "%DST%" mkdir "%DST%"
robocopy "%SRC%" "%DST%" /E /MOVE /R:1 /W:1 /NFL /NDL /NJH /NJS /NP >nul
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 (
  echo [WARN] Falha migrando %LABEL% (robocopy rc=%RC%)
  endlocal & exit /b 1
)
echo [INFO] Migracao %LABEL% concluida.
endlocal & exit /b 0

:fail_with_help
echo.
echo =======================================================
echo [ERRO] Ambiente nao esta pronto para CUDA/WhisperX.
echo GUIA DE SOLUCAO PARA AMBIENTE RESTAURADO:
echo.
echo   1^) Instalar Python 3.11 (melhor estabilidade p/ PyTorch/Whisper)
echo      Download: https://www.python.org/downloads/windows/
echo      - Nao esqueca de marcar "Add python to PATH".
echo.
echo   2^) Baixar e configurar o FFmpeg (build full shared):
echo      Download: https://github.com/GyanD/codexffmpeg/releases
echo      Extraia para que os binarios fiquem em:
echo      %FFMPEG_SHARED_BIN%
echo.
echo   3^) Recrear ambiente virtual local forcado:
echo      Comando: executar.bat --setup-env --recreate-venv
echo =======================================================
echo.
pause
endlocal
exit /b 1

:fail
echo.
echo Execucao interrompida.
pause
endlocal
exit /b 1

:fail_keep_rc
echo.
echo Execucao interrompida.
pause
endlocal
exit /b %RC%

:ok
endlocal
exit /b 0
