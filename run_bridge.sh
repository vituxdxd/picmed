#!/data/data/com.termux/files/usr/bin/bash

# Redireciona tudo para o log para diagnóstico
LOG_FILE="/data/data/com.termux/files/home/picmed/bridge.log"
exec > "$LOG_FILE" 2>&1

echo "--- Iniciando Bridge USB ---"
echo "Data: $(date)"
echo "Argumentos: $@"

# Configura o caminho da biblioteca para o ptyserial
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/data/data/com.termux/files/home/picmed/Termux-serial-tty/bin

# O termux-usb passa o FD como o último argumento. 
# Se chamado via 'termux-usb -e ./run_bridge.sh /dev/bus/usb/xxx/yyy', 
# o FD será o segundo argumento ($2). Se chamado diretamente com o FD, será o primeiro ($1).
FD="${@: -1}"

if [ -z "$FD" ] || [[ ! "$FD" =~ ^[0-9]+$ ]]; then
    echo "Erro: FD não fornecido ou inválido ($FD)."
    echo "Uso correto: termux-usb -e $0 <dispositivo>"
    exit 1
fi
echo "Utilizando FD: $FD"

VIRTUAL_DEVICE="$HOME/ttyesp32"
rm -f "$VIRTUAL_DEVICE"

# Função para monitorar o log e criar o link simbólico assim que o PTY for criado
(
    # Aguarda o ptyserial iniciar e escrever no log
    # Usamos o próprio LOG_FILE que o exec abriu
    tail -f "$LOG_FILE" | while read line; do
        if [[ "$line" == *"PTY created:"* ]]; then
            PTY=$(echo "$line" | awk -F': ' '{print $2}' | tr -d '\r\n')
            if [ -n "$PTY" ]; then
                ln -sf "$PTY" "$VIRTUAL_DEVICE"
                echo "[BRIDGE] Sucesso: $PTY vinculado a $VIRTUAL_DEVICE"
                break
            fi
        fi
    done
) &

# Executa o ptyserial. O sleep mantém a ponte aberta.
echo "Executando ptyserial..."
/data/data/com.termux/files/home/picmed/Termux-serial-tty/ptyserial /data/data/com.termux/files/usr/bin/sleep 10000 "$FD"
