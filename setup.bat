@echo off
chcp 65001 >nul 2>&1
setlocal

echo.
echo ============================================================
echo   AgentTradSyst - Instalacao e Verificacao
echo ============================================================
echo.

:: --- Verificar Python ---
echo [1/4] Verificando Python...

py -3.13 --version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=*" %%v in ('py -3.13 --version 2^>^&1') do set PYVER=%%v
    echo       OK - %PYVER%
    set PYCMD=py -3.13
    goto :check_git
)

py -3.12 --version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=*" %%v in ('py -3.12 --version 2^>^&1') do set PYVER=%%v
    echo       OK - %PYVER%
    set PYCMD=py -3.12
    goto :check_git
)

python --version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
    echo       OK - %PYVER% (usando 'python')
    set PYCMD=python
    goto :check_git
)

echo.
echo  [ERRO] Python nao encontrado.
echo         Baixe e instale em: https://www.python.org/downloads/
echo         IMPORTANTE: marque "Add Python to PATH" durante a instalacao.
echo.
pause
exit /b 1

:check_git
:: --- Verificar Git ---
echo [2/4] Verificando Git...
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [ERRO] Git nao encontrado.
    echo         Baixe e instale em: https://git-scm.com/downloads
    echo         O Git e necessario para instalar o PyQuotex.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('git --version 2^>^&1') do echo       OK - %%v

:: --- Instalar dependencias ---
echo [3/4] Instalando dependencias (pode demorar alguns minutos)...
echo.
%PYCMD% -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo  [ERRO] Falha ao instalar dependencias.
    echo         Verifique sua conexao com a internet e tente novamente.
    echo.
    pause
    exit /b 1
)
echo.
echo       OK - Dependencias instaladas.

:: --- Configurar .env ---
echo [4/4] Verificando arquivo .env...
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo.
        echo  [AVISO] Arquivo .env criado a partir do .env.example
        echo          IMPORTANTE: Abra o arquivo .env e preencha suas credenciais
        echo          antes de iniciar o sistema.
        echo.
        echo          Arquivo: %cd%\.env
        echo.
        echo  Pressione qualquer tecla para abrir o .env no Bloco de Notas...
        pause >nul
        notepad "%cd%\.env"
    ) else (
        echo  [AVISO] .env.example nao encontrado. Crie o arquivo .env manualmente.
    )
) else (
    echo       OK - .env ja existe.
)

:: --- Salva o comando Python encontrado para o iniciar.bat ---
echo %PYCMD%> .pycmd
echo.
echo ============================================================
echo   Instalacao concluida!
echo ============================================================
echo.

:: --- Pergunta se quer iniciar agora ---
set /p INICIAR="  Deseja iniciar o sistema agora? (S/N): "
if /i "%INICIAR%"=="S" (
    echo.
    endlocal
    call iniciar.bat
) else (
    echo.
    echo   Para iniciar depois, execute: iniciar.bat
    echo.
    pause
    endlocal
)
