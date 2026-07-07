@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Portal de Chamados Bondmann - Dashboard local

echo(
echo ===========================================================
echo   Portal de Chamados Bondmann - Dashboard local
echo   Verifica dependencias, instala o que faltar e sobe o app
echo ===========================================================
echo(

REM ----------------------------------------------------------------
REM 1) Python 3.12 - detecta; se faltar, INSTALA automaticamente.
REM    A deteccao roda "import sys" de verdade para ignorar o stub
REM    0-byte da Microsoft Store (aparece no PATH sem ser Python).
REM    Instalacao: 1o via winget, 2o baixando o instalador oficial.
REM ----------------------------------------------------------------
call :detect_python
if not defined BASEPY (
  echo [setup] Python nao encontrado - instalando o Python 3.12 automaticamente...
  call :install_python_winget
  call :detect_python
)
if not defined BASEPY (
  call :install_python_download
  call :detect_python
)
if not defined BASEPY (
  echo(
  echo [ERRO] Nao consegui instalar o Python 3.12 automaticamente.
  echo        Verifique sua conexao com a internet, ou instale manualmente em
  echo        https://www.python.org/downloads/ marcando "Add python.exe to PATH",
  echo        e rode este arquivo novamente.
  goto :fim_erro
)
echo [ok] Python: %BASEPY%

REM ----------------------------------------------------------------
REM 2) Ambiente virtual (.venv)
REM    Valida se o .venv realmente FUNCIONA. Um .venv copiado de outra
REM    maquina (ou de outra pasta) guarda um caminho fixo em pyvenv.cfg
REM    e quebra com "No Python at '...'". Nesse caso, recria do zero.
REM ----------------------------------------------------------------
set "PY=.venv\Scripts\python.exe"
if exist "%PY%" (
  "%PY%" -c "import sys" >nul 2>nul
  if errorlevel 1 (
    echo [aviso] .venv invalido ^(copiado de outra maquina/pasta^) - recriando...
    rmdir /s /q ".venv"
  )
)
if not exist "%PY%" (
  echo [setup] Criando ambiente virtual .venv ...
  %BASEPY% -m venv .venv
  if errorlevel 1 ( echo [ERRO] Falha ao criar o ambiente virtual. & goto :fim_erro )
)

REM ----------------------------------------------------------------
REM 3) Dependencias Python (instala so se faltar alguma)
REM ----------------------------------------------------------------
"%PY%" -c "import fastapi, uvicorn, asyncpg, jwt, jinja2, itsdangerous, slowapi, tzdata" >nul 2>nul
if errorlevel 1 (
  echo [setup] Instalando dependencias Python ^(requirements.txt^) ...
  "%PY%" -m pip install --upgrade pip >nul
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 ( echo [ERRO] Falha ao instalar dependencias Python. & goto :fim_erro )
) else (
  echo [ok] Dependencias Python ja instaladas.
)

REM ----------------------------------------------------------------
REM 4) CSS (Tailwind) - precisa do Node/npm. Se o npm faltar E o
REM    app.css ainda nao existir, tenta instalar o Node.js LTS via
REM    winget. Se o app.css ja existe, o Node nao e necessario agora.
REM ----------------------------------------------------------------
if exist "app\static\css\app.css" (
  echo [ok] CSS ja presente ^(app.css^) - Node.js nao e necessario agora.
) else (
  call :detect_npm
  if not defined NPM (
    echo [setup] Node.js nao encontrado - instalando o Node.js LTS ^(para gerar o CSS^)...
    call :install_node_winget
    call :detect_npm
  )
  if defined NPM (
    if not exist "node_modules" (
      echo [setup] Instalando pacotes do npm ...
      call "%NPM%" install --silent
    )
    echo [setup] Gerando CSS ^(Tailwind^) ...
    call "%NPM%" run build:css
  ) else (
    echo [aviso] Node.js indisponivel e app.css ausente - os estilos podem faltar.
    echo         Instale o Node.js ^(https://nodejs.org^) e rode novamente.
  )
)

REM ----------------------------------------------------------------
REM 5) .env (config) - avisa se faltar
REM ----------------------------------------------------------------
if not exist ".env" (
  echo [aviso] Arquivo .env nao encontrado - copiando de .env.example.
  echo         PREENCHA DATABASE_URL e SUPABASE_* antes de usar de verdade.
  copy ".env.example" ".env" >nul
)

REM ----------------------------------------------------------------
REM 6) Sobe o servidor e abre o navegador
REM    Porta 8000 (livre). SEM --reload: processo unico, para com Ctrl+C.
REM    O navegador abre depois de ~5s, quando o servidor ja esta no ar
REM    (evita o "ERR_CONNECTION_REFUSED" por abrir cedo demais).
REM ----------------------------------------------------------------
set "PORT=8000"
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :%PORT% ^| findstr LISTENING') do (
  echo [setup] Liberando porta %PORT% ^(parando processo %%a antigo^)...
  taskkill /f /pid %%a >nul 2>&1
)
echo(
echo [ok] Iniciando em http://localhost:%PORT%
echo      Login de teste: ti@ / rh@ / func@bondmann.com.br  (senha definida no seu Supabase)
echo      Pressione Ctrl+C nesta janela para parar o servidor.
echo(
start "" /min cmd /c "timeout /t 5 >nul & start "" http://localhost:%PORT%"
"%PY%" -m uvicorn app.main:app --port %PORT% --reload
goto :fim

:fim_erro
echo(
echo *** Setup interrompido. Corrija o item acima e rode novamente. ***
:fim
echo(
pause
endlocal
exit /b


REM ================================================================
REM  Subrotinas
REM ================================================================

REM --- Detecta um Python 3.x que EXECUTA e guarda em BASEPY -------
REM     (vazio se nao houver). Usa caminhos absolutos como fallback
REM     porque, logo apos instalar, o PATH da sessao ainda esta velho.
:detect_python
set "BASEPY="
for %%C in ("py -3" "python") do (
  %%~C -c "import sys" >nul 2>nul
  if not errorlevel 1 ( set "BASEPY=%%~C" & goto :eof )
)
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
  "%ProgramFiles%\Python312\python.exe"
  "C:\Python312\python.exe"
) do (
  if exist "%%~P" (
    "%%~P" -c "import sys" >nul 2>nul
    if not errorlevel 1 ( set BASEPY="%%~P"&goto :eof )
  )
)
goto :eof

REM --- Instala o Python 3.12 via winget (silencioso, escopo usuario)
:install_python_winget
winget --version >nul 2>nul
if errorlevel 1 goto :eof
echo [setup] Instalando Python 3.12 via winget ^(pode levar 1-2 min^)...
winget install -e --id Python.Python.3.12 --silent --scope user --accept-source-agreements --accept-package-agreements
goto :eof

REM --- Fallback: baixa o instalador oficial e instala em silencio ---
:install_python_download
set "PYVER=3.12.10"
set "PYINST=%TEMP%\python-%PYVER%-amd64.exe"
echo [setup] Baixando o instalador oficial do Python %PYVER%...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe' -OutFile '%PYINST%' } catch { exit 1 }"
if errorlevel 1 ( echo [aviso] Falha ao baixar o instalador do Python. & goto :eof )
echo [setup] Instalando Python %PYVER% ^(silencioso, sem admin^)...
"%PYINST%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1
del "%PYINST%" >nul 2>nul
goto :eof

REM --- Detecta o npm e guarda em NPM (vazio se nao houver) --------
:detect_npm
REM NPM fica sem aspas; quem chama usa "%NPM%" (ver secao 4).
set "NPM="
where npm >nul 2>nul
if not errorlevel 1 ( set "NPM=npm" & goto :eof )
if exist "%ProgramFiles%\nodejs\npm.cmd" ( set "NPM=%ProgramFiles%\nodejs\npm.cmd" & goto :eof )
if exist "!ProgramFiles(x86)!\nodejs\npm.cmd" ( set "NPM=!ProgramFiles(x86)!\nodejs\npm.cmd" & goto :eof )
goto :eof

REM --- Instala o Node.js LTS via winget (best-effort) ------------
:install_node_winget
winget --version >nul 2>nul
if errorlevel 1 goto :eof
echo [setup] Instalando Node.js LTS via winget...
winget install -e --id OpenJS.NodeJS.LTS --silent --accept-source-agreements --accept-package-agreements
goto :eof
