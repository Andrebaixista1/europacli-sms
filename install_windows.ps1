Param(
  [switch]$InstallGammu
)

$ErrorActionPreference = "Stop"
$version = "linux-mint-artemis1"

Write-Host "Europa CLI - Sender SMS installer (versao $version)"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
  Write-Host "Erro: Python nao encontrado. Instale o Python 3 e reexecute." -ForegroundColor Red
  exit 1
}

python -m pip install --upgrade pip
python -m pip install windows-curses pyserial

$gammu = Get-Command gammu -ErrorAction SilentlyContinue
if (-not $gammu) {
  Write-Host "Gammu nao encontrado." -ForegroundColor Yellow
  if ($InstallGammu) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
      try {
        winget install --id Gammu.Gammu -e --source winget
      } catch {
        Write-Host "Falha ao instalar Gammu via winget. Instale manualmente." -ForegroundColor Yellow
      }
    } else {
      Write-Host "winget nao encontrado. Instale o Gammu manualmente." -ForegroundColor Yellow
    }
  } else {
    Write-Host "Instale o Gammu para Windows e garanta 'gammu.exe' no PATH." -ForegroundColor Yellow
  }
}

Write-Host "Instalacao concluida." -ForegroundColor Green
