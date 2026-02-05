# Europa CLI - Sender SMS (Gammu)

CLI estilo "tela preta" com navegacao por setas, selecao de modems e disparo de SMS usando Gammu.

## Requisitos
- Linux
- `gammu` instalado
- Modems USB conectados (ex: /dev/ttyUSB0, /dev/ttyACM0)

## Executar
```bash
cd /home/andrefelipe/projetos/sms-cli
python3 sms_cli.py
```

## Versao
- linux-mint-artemis1

## Teclas
- Setas: navegar
- Enter: selecionar
- Espaco: marcar modem
- R: rescan de modems
- F2: finalizar entrada de texto
- ESC: cancelar entrada
- Rescan automatico a cada 30s na tela de modems
- R: reenviar falhas (aparece apos envio com erro)

## Configuracoes
- Prefixo do pais (padrao: 55)
- Flash SMS (toggle)
- Connection do Gammu (padrao: at)
- Delay entre envios (segundos)
- Validacao de modems (usa `gammu identify`)

## Log
- `sms_cli.log`

## Historico de SMS (7 dias)
- Historico em `sms_history.jsonl` (nome, telefone, mensagem, flash, status, modem).
- Registros com mais de 7 dias sao descartados automaticamente.

## API de historico
```bash
python3 sms_api.py --host 0.0.0.0 --port 8081
```
- GET `/history` -> lista registros
- Parametros opcionais: `since=YYYY-MM-DDTHH:MM:SS` e `limit=100`
- GET `/health` -> ok

## Instalador (Linux Mint/Ubuntu)
```bash
./install_linux.sh
```
Opcional (para evitar conflito com Gammu):
```bash
./install_linux.sh --disable-modemmanager
```

## Windows
- Instale o Gammu para Windows e garanta `gammu.exe` no PATH.
- Instale dependencias Python: `windows-curses` e `pyserial`.

Instalador:
```powershell
./install_windows.ps1
```
Opcional (tentar instalar Gammu via winget):
```powershell
./install_windows.ps1 -InstallGammu
```

## Observacoes
- Numeros sao deduplicados (nao envia o mesmo numero em outro chip)
- Envio balanceado entre os modems selecionados (round-robin)
- Importacao CSV com colunas `nome` e `numero` (seletor via zenity/kdialog, com fallback para caminho manual)
- Opcao \"Liberar portas\" mata processos usando as portas selecionadas
- Menu de modems mostra status `OK`/`FAIL`
- Salva o ultimo CSV usado para reusar rapido
- Menu de modems mostra o numero do chip quando disponivel
# europacli-sms
