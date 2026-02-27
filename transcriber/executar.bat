@echo off
title Transcritor Whisper.cpp - Menu Principal
cls
echo ========================================================
echo   CENTRAL DE TRANSCRICAO (Whisper.cpp Wrapper)
echo ========================================================
echo.
echo Verificando ambiente Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado. Instale o Python 3.13 e adicione ao PATH.
    pause
    exit
)

:MENU
cls
echo ========================================================
echo   ESCOLHA O MODO DE OPERACAO
echo ========================================================
echo.
echo [1] Modo Prompt de Comando (CLI Interativa)
echo [2] Modo Interface Visual (GUI)
echo.
set /p opcao="Digite o numero (1 ou 2): "

if "%opcao%"=="1" goto CLI
if "%opcao%"=="2" goto GUI
goto ERROR

:CLI
cls
echo Iniciando Script Mestre (CLI)...
python transcritor_master.py
pause
exit

:GUI
cls
echo Iniciando Interface Visual...
python interface_whisper.py
exit

:ERROR
echo Opcao invalida! Tente novamente.
pause
goto MENU