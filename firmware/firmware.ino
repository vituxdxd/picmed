// =============================================================================
// PROJETO : Monitoramento ECG/VFC — PICMED UNICEPLAC
// HARDWARE: ESP32 (USB-C) + AD8232 + ADS1115
//           Comunicação serial: CH340
//           Alimentação: via regulador 3.3V do ESP32 (módulos com bypass caps)
//           Filtros analógicos: nenhum (avaliação gradativa)
//
// VERSÃO  : 3.5
//
// MUDANÇAS v3.5:
//   • [FIX] Byte baixo do registrador de configuração corrigido: 0x81 → 0xA5.
//     0x81 configurava DR=100 (128 SPS) em vez de DR=101 (250 SPS), causando
//     amostragem a ~128 Hz. Com 250 amostras esperadas/s mas apenas 128 reais,
//     o loop() acumulava totalAmostras mais devagar — porém a leitura serial
//     estava recebendo pacotes duplicados (ADS1115 retornando mesma conversão),
//     inflando a contagem e encerrando a sessão antes do tempo.
//     0xA5 = DR=101 (250 SPS) | COMP_LAT=1 | COMP_QUE=01.
//     COMP_LAT=1 (retentivo): ALRT permanece LOW até getLastConversionResults()
//     ser chamado, evitando re-disparos por ruído no fio.
//   • [FIX] Encerramento de sessão migrado de contagem de amostras para
//     millis(). Contagem dependia da precisão do clock do ADS1115 e do
//     registrador correto — millis() usa o oscilador interno do ESP32 e
//     não é afetado por bugs de configuração do ADC.
//   • [NEW] Duração da sessão recebida do Python via protocolo:
//     "#INICIAR:N" onde N é a duração em segundos. Sem N, usa 300s como
//     fallback. Remove a constante compilada DURACAO_SESSAO_S.
//
// MUDANÇAS v3.4:
//   • Timer hardware (860 Hz) removido. ALRT/RDY do ADS1115 controla o
//     ritmo de aquisição via interrupção de borda de descida (GPIO 33).
//   • configurarADS_AlertReady(): configura ADS1115 via Wire direto.
//   • ISR renomeada de onSampleTimer → onAlertReady.
//
// MUDANÇAS v3.3:
//   • [FIX] processarComando("#CONECTAR"): re-anuncia estado para CH340.
//   • [NEW] Heartbeat #ESTADO:DESCONECTADO a cada 2s.
//
// MUDANÇAS v3.2:
//   • Removido botão físico (BTN_PIN, verificarBotao, debounce).
//
// PROTOCOLO BINÁRIO (ESP32 → Python, apenas dados ECG):
//   Byte 0: 0xAA        — sync byte (nunca aparece em texto ASCII)
//   Byte 1: FLAGS_SEQ   — bit 7 = leads_on, bits 6-0 = sequência (0-127)
//   Byte 2: ADC_HI      — byte alto do valor raw 16-bit (signed)
//   Byte 3: ADC_LO      — byte baixo do valor raw 16-bit
//   Byte 4: CHECKSUM    — XOR de bytes 1, 2 e 3
//   Total : 5 bytes × 250 Hz = 1.250 B/s (< 2% de 115200 baud)
//
// PROTOCOLO TEXTO (bidirecional):
//   Python → ESP32: #CONECTAR | #INICIAR[:N] | #PARAR | #DESCONECTAR
//   ESP32 → Python: #ESTADO:X | #INICIO_SESSAO | #FIM_SESSAO | #CONFIG:...
//
// FIAÇÃO I2C  : SDA → GPIO 21 | SCL → GPIO 22
// FIAÇÃO ALRT : ALRT/RDY (ADS1115) → GPIO 33 (pull-up interno no ESP32)
// =============================================================================

#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <WiFi.h>

// ─── PINOS ────────────────────────────────────────────────────────────────────
const int LO_PLUS      = 34;   // AD8232 — lead-off eletrodo (+) (input-only)
const int LO_MINUS     = 35;   // AD8232 — lead-off eletrodo (−) (input-only)
const int LED_R        = 4;    // LED RGB — vermelho
const int LED_G        = 16;   // LED RGB — verde
const int LED_B        = 17;   // LED RGB — azul
const int ALRT_RDY_PIN = 33;   // ADS1115 ALRT/RDY — data-ready (open-drain, ativo-baixo)

// ─── ENDEREÇO I2C DO ADS1115 ──────────────────────────────────────────────────
const uint8_t ADS_ADDR = 0x48; // ADDR → GND

// ─── CONSTANTES ───────────────────────────────────────────────────────────────
const uint8_t  SYNC_BYTE          = 0xAA;
const uint32_t FS_RAW             = 250;           // Hz — taxa real do ADS1115
const uint32_t DURACAO_FALLBACK_S = 300;           // segundos — usado se #INICIAR vier sem valor

// ─── MÁQUINA DE ESTADOS ───────────────────────────────────────────────────────
enum Estado {
  INICIALIZANDO,
  DESCONECTADO,
  CONECTADO_IDLE,
  LENDO_ECG,
  ELETRODO_OFF,
  ERRO_ADS1115,
  ERRO_AD8232,
};
Estado estadoAtual   = INICIALIZANDO;
bool   pythonConectado = false;

// ─── OBJETOS E VARIÁVEIS GLOBAIS ──────────────────────────────────────────────
Adafruit_ADS1115 ads;

// Flag de amostra pronta — setada pela ISR do ALRT/RDY, lida no loop()
volatile bool sampleReady = false;

uint32_t      totalAmostras  = 0;    // contador diagnóstico (reportado no #FIM_SESSAO)
uint8_t       seqCounter     = 0;    // sequência 0-127 do protocolo binário

// Controle de duração por tempo real (millis), não por contagem de amostras
unsigned long inicioSessaoMs = 0;    // millis() no momento do #INICIO_SESSAO
unsigned long duracaoMs      = 0;    // duração da sessão em milissegundos

// LED
unsigned long ultimoTempoLed = 0;
bool          ledState       = false;

// Buffer de comandos seriais
String bufferCmd = "";

// ─── LED RGB (cátodo comum) ───────────────────────────────────────────────────
#define LED_ANODO_COMUM false

void setLED(bool r, bool g, bool b) {
  if (LED_ANODO_COMUM) {
    digitalWrite(LED_R, !r); digitalWrite(LED_G, !g); digitalWrite(LED_B, !b);
  } else {
    digitalWrite(LED_R,  r); digitalWrite(LED_G,  g); digitalWrite(LED_B,  b);
  }
}
void ledApagado()  { setLED(false, false, false); }
void ledVerde()    { setLED(false, true,  false); }
void ledVermelho() { setLED(true,  false, false); }
void ledAzul()     { setLED(false, false, true);  }
void ledAmarelo()  { setLED(true,  true,  false); }

void piscaLED(bool r, bool g, bool b, unsigned long intervalo) {
  unsigned long agora = millis();
  if (agora - ultimoTempoLed >= intervalo) {
    ultimoTempoLed = agora;
    ledState = !ledState;
    if (ledState) setLED(r, g, b);
    else          ledApagado();
  }
}

void atualizarLED() {
  switch (estadoAtual) {
    case INICIALIZANDO:  piscaLED(false, false, true,  150); break; // azul piscando
    case DESCONECTADO:   ledAzul();                           break; // azul fixo
    case CONECTADO_IDLE: ledVerde();                          break; // verde fixo
    case LENDO_ECG:      piscaLED(false, true,  false, 300); break; // verde piscando
    case ELETRODO_OFF:   piscaLED(true,  false, false, 400); break; // vermelho piscando
    case ERRO_ADS1115:   ledAmarelo();                        break; // amarelo fixo
    case ERRO_AD8232:    ledVermelho();                       break; // vermelho fixo
  }
}

// ─── ISR DO ALRT/RDY ──────────────────────────────────────────────────────────
// Dispara na borda de descida: ADS1115 puxa ALRT LOW ao finalizar conversão.
// Apenas seta a flag — leitura I2C ocorre no loop() para evitar conflitos com Wire.
void IRAM_ATTR onAlertReady() {
  sampleReady = true;
}

// ─── CONFIGURAÇÃO DO ADS1115 COM ALRT/RDY ─────────────────────────────────────
// Configura via Wire direto (a biblioteca Adafruit não expõe COMP_QUE).
//
// Truque datasheet §9.3.8: Lo_thresh=0x0000 e Hi_thresh=0x8000 com COMP_QUE≠11
// desativam o comparador de tensão e fazem ALRT/RDY pulsar a cada conversão.
//
// Config register (0x01) = 0x42A5:
//   Byte alto 0x42: OS=0 | MUX=100 (AIN0/GND) | PGA=001 (±4.096V) | MODE=0 (contínuo)
//   Byte baixo 0xA5:
//     DR    [7:5] = 101  → 250 SPS              ← corrigido em v3.5 (era 0x81 = 128 SPS)
//     COMP_MODE[4]= 0    → comparador tradicional
//     COMP_POL [3]= 0    → ALRT ativo em nível baixo
//     COMP_LAT [2]= 1    → retentivo: ALRT permanece LOW até ler o registrador
//     COMP_QUE[1:0]= 01  → aciona após 1 conversão (habilita o pino)
void configurarADS_AlertReady() {
  // Registrador de configuração
  Wire.beginTransmission(ADS_ADDR);
  Wire.write(0x01);   // aponta para Config register
  Wire.write(0x42);   // byte alto
  Wire.write(0xA5);   // byte baixo — 250 SPS, COMP_LAT=1, COMP_QUE=01
  Wire.endTransmission();

  // Lo_thresh = 0x0000
  Wire.beginTransmission(ADS_ADDR);
  Wire.write(0x02);
  Wire.write(0x00); Wire.write(0x00);
  Wire.endTransmission();

  // Hi_thresh = 0x8000 — MSB=1 ativa modo data-ready no ALRT/RDY
  Wire.beginTransmission(ADS_ADDR);
  Wire.write(0x03);
  Wire.write(0x80); Wire.write(0x00);
  Wire.endTransmission();
}

// ─── TRANSIÇÕES DE ESTADO ─────────────────────────────────────────────────────
void setEstado(Estado novo) {
  estadoAtual = novo;
  switch (novo) {
    case DESCONECTADO:   Serial.println("#ESTADO:DESCONECTADO");   break;
    case CONECTADO_IDLE: Serial.println("#ESTADO:CONECTADO_IDLE"); break;
    case LENDO_ECG:      Serial.println("#ESTADO:LENDO_ECG");      break;
    case ELETRODO_OFF:   Serial.println("#ESTADO:ELETRODO_OFF");   break;
    case ERRO_ADS1115:   Serial.println("#ESTADO:ERRO_ADS1115");   break;
    case ERRO_AD8232:    Serial.println("#ESTADO:ERRO_AD8232");    break;
    default: break;
  }
}

// ─── INÍCIO E FIM DE LEITURA ──────────────────────────────────────────────────
void iniciarLeitura(uint32_t duracaoSeg) {
  if (estadoAtual != CONECTADO_IDLE) return;
  totalAmostras  = 0;
  seqCounter     = 0;
  sampleReady    = false;
  inicioSessaoMs = millis();              // referência de tempo real
  duracaoMs      = duracaoSeg * 1000UL;  // converte para ms
  Serial.println("#INICIO_SESSAO");
  bool leadsOk = (digitalRead(LO_PLUS) == LOW && digitalRead(LO_MINUS) == LOW);
  setEstado(leadsOk ? LENDO_ECG : ELETRODO_OFF);
}

void pararLeitura() {
  if (estadoAtual != LENDO_ECG && estadoAtual != ELETRODO_OFF) return;
  unsigned long duracaoRealMs = millis() - inicioSessaoMs;
  Serial.printf("#FIM_SESSAO — %lu amostras, %lu ms reais.\n",
                totalAmostras, duracaoRealMs);
  setEstado(pythonConectado ? CONECTADO_IDLE : DESCONECTADO);
}

// ─── ENVIO DE AMOSTRA BINÁRIA ─────────────────────────────────────────────────
// Pacote: [SYNC][FLAGS_SEQ][ADC_HI][ADC_LO][CHECKSUM]
void enviarAmostraBinaria(int16_t adc_raw, bool leads_on) {
  uint8_t pkt[5];
  pkt[0] = SYNC_BYTE;
  pkt[1] = (leads_on ? 0x80 : 0x00) | (seqCounter & 0x7F);
  pkt[2] = (uint8_t)((adc_raw >> 8) & 0xFF);
  pkt[3] = (uint8_t)(adc_raw & 0xFF);
  pkt[4] = pkt[1] ^ pkt[2] ^ pkt[3];
  Serial.write(pkt, 5);
  seqCounter = (seqCounter + 1) & 0x7F;
}

// ─── COMANDOS SERIAIS ─────────────────────────────────────────────────────────
void processarComando(String cmd) {
  cmd.trim();

  if (cmd.startsWith("#INICIAR")) {
    // Protocolo: "#INICIAR:N" onde N = duração em segundos
    // Se vier apenas "#INICIAR" (sem N), usa DURACAO_FALLBACK_S
    uint32_t duracao = DURACAO_FALLBACK_S;
    int idx = cmd.indexOf(':');
    if (idx != -1) {
      int32_t val = cmd.substring(idx + 1).toInt();
      if (val > 0 && val <= 3600) duracao = (uint32_t)val;
    }
    iniciarLeitura(duracao);

  } else if (cmd == "#PARAR") {
    pararLeitura();

  } else if (cmd == "#CONECTAR") {
    pythonConectado = true;
    if (estadoAtual == DESCONECTADO) {
      setEstado(CONECTADO_IDLE);
    } else {
      // [FIX v3.3] CH340 não reseta o ESP32 ao abrir a porta.
      // Re-anuncia o estado atual para que o Python sincronize.
      setEstado(estadoAtual);
    }

  } else if (cmd == "#DESCONECTAR") {
    pythonConectado = false;
    if (estadoAtual == CONECTADO_IDLE) {
      setEstado(DESCONECTADO);
    } else if (estadoAtual == LENDO_ECG) {
      pararLeitura();
    } else if (estadoAtual == ELETRODO_OFF) {
      Serial.println("#FIM_SESSAO_PARCIAL");
      setEstado(DESCONECTADO);
    }
  }
}

void lerComandosSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (bufferCmd.length() > 0) {
        processarComando(bufferCmd);
        bufferCmd = "";
      }
    } else {
      bufferCmd += c;
      if (bufferCmd.length() > 64) bufferCmd = ""; // overflow protection
    }
  }
}

// ─── SETUP ────────────────────────────────────────────────────────────────────
void setup() {
  // Desabilita rádios: reduz ~80 mA e elimina ruído de 2.4 GHz no analógico
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  btStop();

  Serial.setTxBufferSize(512);
  Serial.begin(115200);

  // LEDs
  pinMode(LED_R, OUTPUT);
  pinMode(LED_G, OUTPUT);
  pinMode(LED_B, OUTPUT);
  ledApagado();

  // AD8232 lead-off
  pinMode(LO_PLUS,  INPUT);
  pinMode(LO_MINUS, INPUT);

  // ALRT/RDY: pull-up interno (open-drain, ativo-baixo).
  // Interrupção anexada APÓS configurar o ADS1115 para evitar
  // disparos espúrios durante a inicialização do barramento I2C.
  pinMode(ALRT_RDY_PIN, INPUT_PULLUP);

  // Pisca azul durante inicialização (~1.8 s)
  Serial.println("Inicializando ECG Pro v3.5...");
  unsigned long tInit = millis();
  while (millis() - tInit < 1800) {
    atualizarLED();
    delay(10);
  }

  // I2C Fast Mode (400 kHz — reduz tempo de leitura para ~150 µs)
  Wire.setClock(400000);

  // Detecta ADS1115
  if (!ads.begin(ADS_ADDR)) {
    Serial.println("ERRO: ADS1115 nao encontrado!");
    setEstado(ERRO_ADS1115);
    while (true) { atualizarLED(); delay(10); }
  }

  // Verifica sinal do AD8232 antes de reconfigurar o ADS1115.
  // Usa modo contínuo padrão da Adafruit apenas para esta checagem.
  ads.setGain(GAIN_ONE);
  ads.setDataRate(RATE_ADS1115_250SPS);
  ads.startADCReading(ADS1X15_REG_CONFIG_MUX_SINGLE_0, /*continuous=*/true);
  delay(20);
  int32_t somaAbs = 0;
  for (int i = 0; i < 20; i++) {
    delay(5);
    somaAbs += abs(ads.getLastConversionResults());
  }
  if (somaAbs == 0) {
    Serial.println("ERRO: AD8232 sem sinal!");
    setEstado(ERRO_AD8232);
    while (true) { atualizarLED(); delay(10); }
  }

  // Reconfigura o ADS1115 para ALRT/RDY como data-ready (250 SPS, retentivo).
  // Esta chamada sobrescreve a configuração da Adafruit feita acima.
  configurarADS_AlertReady();

  // Lê uma vez para garantir que o primeiro pulso ALRT/RDY seja gerado
  // e que qualquer nível residual no pino seja limpo antes do attachInterrupt.
  delay(10);
  ads.getLastConversionResults();
  sampleReady = false;

  // A partir daqui o ADS1115 controla o ritmo: exatamente 250 pulsos/s.
  attachInterrupt(digitalPinToInterrupt(ALRT_RDY_PIN), onAlertReady, FALLING);

  Serial.println("#CONFIG:FS_RAW=250,PROTOCOLO=BIN5,GANHO=GAIN_ONE,FILTROS=NENHUM,ALIM=ESP32,CHIP_SERIAL=CH340,SYNC=ALRT_RDY");
  Serial.println("Pronto. Aguardando #CONECTAR.");
  setEstado(DESCONECTADO);
}

// ─── LOOP ─────────────────────────────────────────────────────────────────────
void loop() {
  lerComandosSerial();
  atualizarLED();

  // Heartbeat em DESCONECTADO: re-envia #ESTADO:DESCONECTADO a cada 2s.
  // Garante handshake com CH340 (que não reseta o ESP32 ao abrir a porta).
  static unsigned long ultimoHeartbeat = 0;
  if (estadoAtual == DESCONECTADO && millis() - ultimoHeartbeat >= 2000) {
    ultimoHeartbeat = millis();
    Serial.println("#ESTADO:DESCONECTADO");
  }

  // Descarta pulsos ALRT/RDY fora de sessão ativa.
  // O ADS1115 continua convertendo em background — apenas ignoramos a flag.
  if (estadoAtual != LENDO_ECG && estadoAtual != ELETRODO_OFF) {
    sampleReady = false;
    return;
  }

  // Encerramento por tempo real — independe da contagem de amostras.
  // millis() usa o oscilador interno do ESP32: não é afetado por bugs
  // de configuração do ADC nem por variações no clock do ADS1115.
  if (millis() - inicioSessaoMs >= duracaoMs) {
    pararLeitura();
    return;
  }

  // Aguarda pulso ALRT/RDY (ISR seta a flag)
  if (!sampleReady) return;
  sampleReady = false;

  // Leitura do ADC: getLastConversionResults() acessa o registrador de
  // conversão via I2C (~150 µs a 400 kHz). Com COMP_LAT=1, esta leitura
  // também limpa o ALRT, prevenindo re-disparos da ISR.
  int16_t valorBruto = ads.getLastConversionResults();

  // Status dos eletrodos
  bool leadsOk = (digitalRead(LO_PLUS) == LOW && digitalRead(LO_MINUS) == LOW);

  if (!leadsOk && estadoAtual != ELETRODO_OFF) {
    setEstado(ELETRODO_OFF);
  } else if (leadsOk && estadoAtual == ELETRODO_OFF) {
    setEstado(LENDO_ECG);
  }

  enviarAmostraBinaria(valorBruto, leadsOk);

  totalAmostras++; // apenas diagnóstico — não controla mais o encerramento
}