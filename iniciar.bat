@echo off
chcp 65001 >nul 2>&1

:: Usa o comando Python salvo pelo setup.bat (se disponivel)
set PYCMD=
if exist ".pycmd" (
    set /p PYCMD=<.pycmd
)

:: Fallback: tenta detectar Python
if "%PYCMD%"=="" (
    py -3.13 --version >nul 2>&1
    if %errorlevel% equ 0 set PYCMD=py -3.13
)
if "%PYCMD%"=="" (
    py -3.12 --version >nul 2>&1
    if %errorlevel% equ 0 set PYCMD=py -3.12
)
if "%PYCMD%"=="" (
    python --version >nul 2>&1
    if %errorlevel% equ 0 set PYCMD=python
)

if "%PYCMD%"=="" (
    echo.
    echo  [ERRO] Python nao encontrado. Execute setup.bat primeiro.
    echo.
    pause
    exit /b 1
)

%PYCMD% main.py
