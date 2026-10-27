# Firmware do ESP32

Este diretório contém duas variantes do firmware de aquisição de ECG. Ambas utilizam ESP32, AD8232 e ADS1115, com a mesma pinagem de aquisição e comunicação. Os pinos do LED RGB mudam conforme a montagem.

| Arquivo | Versão | LEDs RGB (R / G / B) |
|---|---|---|
| [firmware.ino](firmware.ino) | v3.5 | GPIO 4 / 16 / 17 |
| [firmware_ecg2.ino](firmware_ecg2.ino) | v3.6 | GPIO 14 / 12 / 13 |

O [esquema elétrico documentado](../docs/hardware.md) utiliza os pinos de LED da variante v3.6. Confira as conexões do equipamento antes de gravar.

## Conexões comuns

| Sinal | GPIO do ESP32 |
|---|---|
| AD8232 `LO+` | 34 |
| AD8232 `LO−` | 35 |
| ADS1115 `SDA` | 21 |
| ADS1115 `SCL` | 22 |
| ADS1115 `ALRT/RDY` | 33 |

O endereço I2C do ADS1115 é `0x48`, com `ADDR` ligado ao GND. A taxa configurada é de 250 amostras por segundo e a comunicação USB serial utiliza 115200 baud.

## Preparar e gravar

1. Instale o suporte a placas ESP32 da Espressif na Arduino IDE. O endereço do índice de placas está em [bibliotecas arduino.txt](../bibliotecas%20arduino.txt).
2. Instale a biblioteca **Adafruit ADS1X15** e as dependências solicitadas pelo gerenciador de bibliotecas.
3. Escolha a variante correspondente à pinagem do LED. Copie apenas esse arquivo para uma pasta de sketch com o mesmo nome: `firmware/firmware.ino` ou `firmware_ecg2/firmware_ecg2.ino`, fora deste diretório.
4. Abra a cópia na Arduino IDE, selecione a placa ESP32 e a porta serial e faça o upload.
5. Feche o monitor serial antes de conectar a placa pela aplicação Python.

Mantenha apenas uma variante no sketch de gravação. A Arduino IDE compila os arquivos `.ino` da mesma pasta; incluir as duas variantes causa duplicação de definições.

## Comunicação com a aplicação

Os comandos de texto terminam com uma quebra de linha:

| Comando | Ação |
|---|---|
| `#CONECTAR` | Estabelece a conexão lógica com a aplicação. |
| `#INICIAR:300` | Inicia uma coleta de 300 segundos. |
| `#PARAR` | Interrompe a coleta em andamento. |
| `#DESCONECTAR` | Encerra a conexão lógica. |

Durante a coleta, o firmware envia pacotes binários de cinco bytes com sincronização, estado dos eletrodos e sequência, valor do ADC e checksum. As mensagens `#ESTADO`, `#INICIO_SESSAO` e `#FIM_SESSAO` informam o andamento da aquisição.

[Voltar ao README](../README.md)
