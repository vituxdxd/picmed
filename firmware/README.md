# Firmware — variantes de hardware

Este diretório contém o firmware do ESP32 para as duas montagens do equipamento.
O código é o mesmo; mudam apenas os pinos dos LEDs RGB.

| Arquivo | Versão | LEDs RGB (R / G / B) |
|---|---|---|
| `firmware.ino` | v3.5 | GPIO 4 / 16 / 17 |
| `firmware_ecg2.ino` | v3.6 | GPIO 14 / 12 / 13 |

Pinos iguais nas duas variantes:

- AD8232 `LO+` → GPIO 34 · `LO−` → GPIO 35
- ADS1115 `ALRT/RDY` → GPIO 33 · I2C `SDA` → GPIO 21 · `SCL` → GPIO 22
- I2C do ADS1115 no endereço `0x48`

> Antes de gravar, confira no cabeçalho de cada arquivo os pinos da sua montagem.
