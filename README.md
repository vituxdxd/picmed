# PICMED UNICEPLAC — Sistema de Coleta ECG/VFC

Sistema integrado de hardware e software para aquisição, processamento e análise de sinais de Eletrocardiograma (ECG) e Variabilidade da Frequência Cardíaca (VFC/HRV), com foco na identificação de estresse oculto através do **Índice de Baevsky**.

## 🚀 Visão Geral

O projeto foi desenvolvido para apoiar pesquisas clínicas no UNICEPLAC, permitindo a coleta de dados fisiológicos de participantes de forma simplificada e automatizada. O sistema é composto por um firmware customizado para ESP32 e uma aplicação Python multi-camadas que gerencia desde a recepção serial até a análise estatística avançada.

## 🛠️ Arquitetura do Sistema

O projeto é estruturado em 4 camadas principais:

1.  **Camada 1: Aquisição (`camada1_aquisicao.py`)**
    *   Gerencia a comunicação serial com o hardware (ESP32).
    *   Suporta protocolos binários de alta performance e fallback em CSV.
    *   Implementa sistema de *fan-out* (Broadcaster) para distribuição de dados em tempo real.

2.  **Camada 2: Armazenamento (`camada2_banco.py`)**
    *   Persistência em banco de dados SQLite (`estudo_picmed.db`).
    *   Gerenciamento completo de Participantes, Sessões, Amostras Brutas e Métricas.
    *   Otimizado com *Write-Ahead Logging* (WAL) e inserções em lote para alta vazão.

3.  **Camada 3: Processamento (`camada3_processamento.py`)**
    *   Extração de intervalos R-R e picos R utilizando a biblioteca **NeuroKit2**.
    *   Cálculo de métricas temporais: SDNN, RMSSD, pNN50.
    *   Implementação do **Índice de Estresse de Baevsky (SI)** com interpretação clínica automática.

4.  **Camada 4: Servidor e Interface (`app.py`)**
    *   API REST em Flask para controle do sistema.
    *   Streaming de ECG em tempo real via *Server-Sent Events* (SSE).
    *   Interface web para monitoramento, aplicação de questionários (PSS-10, IPAQ, PSQI e STAI) e emissão de relatório PDF.

## 🔌 Hardware

O sistema utiliza os seguintes componentes:
*   **Microcontrolador:** ESP32 (Foco em processamento e comunicação estável).
*   **Sensor ECG:** AD8232 (Front-end analógico para ECG).
*   **Conversor ADC:** ADS1115 (16 bits, configurado para 250 SPS via I2C).
*   **Interface:** Protocolo binário customizado a 115200 baud.

## 💻 Requisitos de Software

*   Python 3.10+
*   Dependências principais:
    *   `Flask`: Servidor web e API.
    *   `pyserial`: Comunicação com o hardware.
    *   `neurokit2`: Processamento de sinais biológicos.
    *   `numpy`: Cálculos numéricos.
    *   `matplotlib`: Geração do gráfico do trecho de ECG no relatório.
    *   `pandoc` + `XeLaTeX` (TeX Live no Linux, MiKTeX no Windows): Geração dos relatórios em PDF via Markdown + XeLaTeX.
    *   `sqlite3` (stdlib do Python): Armazenamento de dados.

## 🚀 Como Executar

### 1. Preparação do Hardware
*   Realize as conexões conforme as definições no arquivo `firmware/firmware.ino`.
*   Carregue o código no ESP32 utilizando a Arduino IDE ou VS Code (PlatformIO).

### 2. Configuração do Ambiente Python
```bash
# Clone o repositório
git clone git@github.com:osnicavalcanti/picmed.git
cd picmed-main

# Crie um ambiente virtual
python3 -m venv .venv
source .venv/bin/activate  # No Windows: .venv\Scripts\activate

# Dependências de sistema para PDF (Linux)
sudo apt-get update && sudo apt-get install -y pandoc texlive-xetex

# Dependências de sistema para PDF (Windows - PowerShell)
winget install --id JohnMacFarlane.Pandoc -e
winget install --id MiKTeX.MiKTeX -e

# Instale as dependências
pip install -r requirements.txt
```

**Windows (PDF):** após instalar, rode as atualizações do MiKTeX (MiKTeX Console → *Updates* → *Check for updates*, ou `mpm --update-db` e `mpm --update`). Reinicie o terminal para atualizar o PATH e confirme `pandoc --version` e `xelatex --version`. Se solicitado, habilite a instalação automática de pacotes no MiKTeX.

### 4. Execução no Termux (Android)
O projeto suporta execução nativa no Android via **Termux** com acesso direto ao hardware via USB OTG. Esta branch foi otimizada para este ambiente, incluindo drivers customizados para comunicação serial.

#### 1. Instalação do Ambiente
No Termux, instale as dependências de sistema (incluindo as necessárias para geração de PDF e processamento de sinal):
```bash
pkg update
pkg install python termux-api libusb clang make libandroid-pty-dev \
            pandoc texlive-bin libjpeg-turbo libpng freetype
```

**Importante (PDF no Termux):** Após instalar o `texlive-bin`, é necessário gerar os formatos do LaTeX manualmente para evitar o erro `I can't find the format file 'xelatex.fmt'`:
```bash
fmtutil-sys --byfmt xelatex
# Recomendado gerar todos os formatos para evitar outros erros:
fmtutil-sys --all
```

Compile o driver serial customizado (necessário para criar a ponte PTY):
```bash
cd Termux-serial-tty && make all ptyserial
cd ..
```

Configure o ambiente virtual Python:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 2. Preparação do Hardware e Permissões
1. Conecte o ESP32 ao smartphone via cabo **USB OTG**.
2. Identifique o caminho do dispositivo USB:
   ```bash
   termux-usb -l
   ```
   *(Exemplo de saída: `/dev/bus/usb/001/002`)*
3. Solicite permissão de acesso (confirme o pop-up que aparecerá no Android):
   ```bash
   termux-usb -r /dev/bus/usb/001/002
   ```

#### 3. Iniciando a Ponte Serial (Bridge)
A ponte é necessária para que o Python acesse o USB via um terminal virtual (PTY). **Mantenha este comando rodando em uma sessão do Termux:**
```bash
termux-usb -e ./run_bridge.sh /dev/bus/usb/001/002
```
*   O script criará um link em `~/ttyesp32`.
*   A ponte foi modificada para desativar RTS/DTR, evitando que o ESP32 reinicie ao conectar.

#### 4. Iniciar a Aplicação
Em uma **nova sessão/aba** do Termux:
```bash
source .venv/bin/activate
python app.py
```
Acesse no navegador do Android: `http://localhost:5000`

---

**Nota sobre a Porta Serial na Interface:**
Ao abrir a interface web, certifique-se de que o campo "Porta Serial" aponta para o link criado pela ponte:
`/data/data/com.termux/files/home/ttyesp32` (ou apenas `~/ttyesp32` se o sistema aceitar a expansão).

## 📊 Funcionalidades da Interface

*   **Aquisição:** Monitoramento em tempo real do sinal ECG e estimativa de BPM.
*   **Participantes:** Cadastro anônimo com controle de ciclo acadêmico.
*   **Coleta:** Gerenciamento de sessões e persistência em banco local (SQLite).
*   **Questionários:** Aplicação digital de PSS-10, IPAQ, PSQI e STAI-S-6/STAI-T-6.
*   **Resultados:** Relatórios em PDF com métricas de VFC, classificações e trecho visual do ECG (Derivação II).

## 📝 Licença

Este projeto é de uso acadêmico e institucional.

---
**PICMED UNICEPLAC** — *Inovação em Saúde e Tecnologia.*
