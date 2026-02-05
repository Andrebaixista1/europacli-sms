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

## Observacoes
- Numeros sao deduplicados (nao envia o mesmo numero em outro chip)
- Envio balanceado entre os modems selecionados (round-robin)
- Importacao CSV com colunas `nome` e `numero` (seletor via zenity/kdialog, com fallback para caminho manual)
- Opcao \"Liberar portas\" mata processos usando as portas selecionadas
- Menu de modems mostra status `OK`/`FAIL`
- Salva o ultimo CSV usado para reusar rapido
- Menu de modems mostra o numero do chip quando disponivel
# europacli-sms
