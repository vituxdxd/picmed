# =============================================================================
# CAMADA 1 — AQUISIÇÃO v3.4
#
# Atualizada para aquisição direta a 250 Hz no ADS1115 (sem decimação):
#   • Recebe pacotes binários de 5 bytes a 250 Hz (dados ECG)
#   • Recebe mensagens texto (comandos, status, marcadores)
#   • Publica amostras a 250 Hz via broadcaster (interface inalterada)
#
# PROTOCOLO BINÁRIO (ESP32 → Python):
#   [0xAA][FLAGS_SEQ][ADC_HI][ADC_LO][CHECKSUM]
#   FLAGS_SEQ: bit 7 = leads_on, bits 6-0 = sequência (0-127)
#   CHECKSUM:  XOR de bytes 1, 2 e 3
#
# PROTOCOLO TEXTO (bidirecional, inalterado):
#   Python → ESP32: #CONECTAR, #INICIAR, #PARAR, #DESCONECTAR
#   ESP32 → Python: #ESTADO:X, #INICIO_SESSAO, #FIM_SESSAO, #CONFIG:...
#
# LOTE DE PUBLICAÇÃO:
#   Amostras são acumuladas em lotes de 25 (100 ms a 250 Hz) antes de
#   serem publicadas via broadcaster — apenas para reduzir overhead de
#   chamadas. Não há decimação nem anti-aliasing por software.
# =============================================================================
#
# CORREÇÕES v3.4:
#   • [ARCH] ADS1115 configurado para 250 SPS direto pelo firmware —
#     decimação 860→250 Hz removida por completo. A função _decimar_chunk
#     e as constantes CHUNK_OUT foram eliminadas.
#   • [FIX] Polaridade do sinal ECG invertida: tensao_mv = −adc_raw × LSB.
#     O sinal estava aparecendo invertido no gráfico devido à configuração
#     diferencial do AD8232 em relação ao ADS1115.
#   • fs_raw renomeado conceitualmente para fs_out (ambos = 250 Hz);
#     o campo fs_raw é mantido no StatusSerial por compatibilidade de API.
#
# CORREÇÕES v3.3:
#   • [FIX] Handshake CH340: reage a #ESTADO:DESCONECTADO enviando #CONECTAR.
#   • [FIX] Fallback por timeout (4s) mantido como segunda linha de defesa.
#
# CORREÇÕES v3.2:
#   • [FIX] Loop de leitura: continue só ocorre se buffer vazio E sem dados.
#
# CORREÇÕES v3.1:
#   • Leitura serial bufferizada; fallback CSV; ring buffer de debug;
#     tolerância a blocos parciais no encerramento de sessão.
# =============================================================================

import threading
import queue
import time
import struct
import serial
import serial.tools.list_ports
from collections import deque
from dataclasses import dataclass
from typing import Optional, List, Tuple


# ─── Estrutura de uma amostra ECG (interface pública, inalterada) ─────────────
@dataclass
class AmostraECG:
    timestamp_us: int
    adc_raw:      int
    tensao_mv:    float
    leads_on:     bool
    idx:          int = 0


# ─── Status da conexão ────────────────────────────────────────────────────────
@dataclass
class StatusSerial:
    conectado:            bool = False
    porta:                str  = ""
    amostras_recebidas:   int  = 0
    amostras_raw:         int  = 0   # mantido por compatibilidade; igual a amostras_recebidas
    amostras_perdidas:    int  = 0
    sessao_ativa:         bool = False
    estado_placa:         str  = "DESCONECTADO"
    erro:                 str  = ""
    fs_raw:               int  = 250  # [v3.4] agora 250 Hz direto (sem decimação)
    fs_out:               int  = 250


# ─── Broadcaster fan-out (inalterado) ─────────────────────────────────────────
class Broadcaster:
    def __init__(self):
        self._filas: list[queue.Queue] = []
        self._lock = threading.Lock()

    def registrar(self, maxsize: int = 1000) -> queue.Queue:
        q = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._filas.append(q)
        return q

    def desregistrar(self, q: queue.Queue):
        with self._lock:
            try: self._filas.remove(q)
            except ValueError: pass

    def publicar(self, amostra: AmostraECG):
        with self._lock:
            for q in self._filas:
                try: q.put_nowait(amostra)
                except queue.Full: pass


# ─── Instâncias globais ───────────────────────────────────────────────────────
broadcaster = Broadcaster()
status      = StatusSerial()

_serial_conn:    Optional[serial.Serial]    = None
_thread_leitura: Optional[threading.Thread] = None
_evento_parar    = threading.Event()
_lock_serial     = threading.Lock()

# Ring buffer de debug — últimos 200 eventos para diagnóstico
_debug_log  = deque(maxlen=200)
_lock_debug = threading.Lock()

# ─── Callback de fim de sessão ────────────────────────────────────────────────
# Registrado por app.py para encerrar a sessão no banco quando o firmware
# termina a leitura por tempo e envia #FIM_SESSAO.
on_fim_sessao: Optional[callable] = None


def _debug(msg: str):
    """Adiciona mensagem ao ring buffer de debug com timestamp."""
    ts = time.strftime("%H:%M:%S")
    with _lock_debug:
        _debug_log.append(f"[{ts}] {msg}")


def obter_debug_log() -> list:
    """Retorna as últimas mensagens de debug."""
    with _lock_debug:
        return list(_debug_log)


# ─── Constantes do protocolo ──────────────────────────────────────────────────
SYNC_BYTE  = 0xAA
PKT_SIZE   = 5
CHUNK_SIZE = 25   # [v3.4] lote de publicação: 25 amostras = 100 ms a 250 Hz
                  # (antes era 86 amostras a 860 Hz para decimação — removido)

# Resolução: GAIN_ONE → 0.125 mV/LSB
ADC_MV_PER_LSB = 0.125


# ─── Parsear linha CSV (fallback para firmware v2) ────────────────────────────
def _parsear_csv(linha: str) -> Optional[Tuple[int, bool]]:
    """Tenta parsear linha CSV: timestamp_us,adc_raw,tensao_mv,leads_on"""
    partes = linha.strip().split(",")
    if len(partes) != 4:
        return None
    try:
        adc_raw  = int(partes[1])
        leads_on = partes[3].strip() == "1"
        return (adc_raw, leads_on)
    except (ValueError, IndexError):
        return None


# ─── Utilitários ──────────────────────────────────────────────────────────────
def listar_portas() -> list[dict]:
    return [
        {"porta":    p.device,
         "descricao": p.description or "Porta Serial",
         "vid_pid":  f"{p.vid}:{p.pid}" if p.vid else "—"}
        for p in serial.tools.list_ports.comports()
    ]


def _enviar_comando(cmd: str):
    with _lock_serial:
        try:
            if _serial_conn and _serial_conn.is_open:
                _serial_conn.write(f"{cmd}\n".encode("utf-8"))
                _serial_conn.flush()
                _debug(f"TX: {cmd}")
        except serial.SerialException:
            pass


def _processar_linha_texto(linha: str):
    """Processa linhas de texto do ESP32 (status, marcadores, etc.)."""
    global status
    _debug(f"TXT: {linha}")

    # CP2102 reseta o ESP32 ao abrir a porta → envia "Pronto." após o boot.
    # CH340 não reseta → "Pronto." pode nunca chegar se o ESP32 já estava rodando.
    # Em ambos os casos, o heartbeat (#ESTADO:DESCONECTADO) e o bloco abaixo
    # garantem o handshake. O "Pronto." continua sendo tratado como redundância.
    if linha.startswith("Pronto"):
        _debug("ESP32 pronto — enviando #CONECTAR")
        _enviar_comando("#CONECTAR")
        return

    if "#INICIO_SESSAO" in linha:
        status.sessao_ativa       = True
        status.amostras_recebidas = 0
        status.amostras_raw       = 0
        return

    if "#FIM_SESSAO" in linha:
        status.sessao_ativa = False
        if on_fim_sessao is not None:
            try:
                on_fim_sessao()
                _debug("callback on_fim_sessao executado")
            except Exception as e:
                _debug(f"ERRO callback on_fim_sessao: {e}")
        return

    if linha.startswith("#ESTADO:"):
        partes = linha.split(":", 1)
        if len(partes) == 2:
            novo_estado = partes[1].strip()
            status.estado_placa = novo_estado

            # [FIX v3.3] Reage a #ESTADO:DESCONECTADO enviando #CONECTAR imediatamente.
            if novo_estado == "DESCONECTADO":
                _debug("Recebido #ESTADO:DESCONECTADO — enviando #CONECTAR")
                _enviar_comando("#CONECTAR")
        return

    if linha.startswith("#CONFIG:"):
        try:
            params = linha.split(":", 1)[1]
            for par in params.split(","):
                chave, valor = par.split("=")
                if chave.strip() == "FS_RAW":
                    status.fs_raw = int(valor.strip())
                    status.fs_out = status.fs_raw  # [v3.4] sem decimação; ambos iguais
        except (ValueError, IndexError):
            pass
        return


# ─── Thread de leitura serial ─────────────────────────────────────────────────
def _loop_leitura(porta: str, baud: int):
    global status, _serial_conn
    idx = 0

    try:
        ser = serial.Serial(porta, baud, timeout=0.5)
        _serial_conn     = ser
        status.conectado = True
        status.porta     = porta
        status.erro      = ""
        _debug(f"Porta {porta} aberta a {baud} baud")

        # Estratégia de handshake (suporta CP2102 e CH340):
        #
        # 1ª linha de defesa — reativa (preferida):
        #   _processar_linha_texto() envia #CONECTAR ao receber "Pronto."
        #   ou #ESTADO:DESCONECTADO (heartbeat do firmware a cada 2s).
        #
        # 2ª linha de defesa — timeout (redundância):
        #   Se após 4s o estado ainda for DESCONECTADO, envia #CONECTAR uma vez.
        _tempo_abertura            = time.time()
        _conectar_fallback_enviado = False

        # Buffers
        byte_buffer = bytearray()
        # [v3.4] lote de amostras para publicação (substituiu raw_samples da decimação)
        lote_amostras: List[tuple] = []   # (adc_raw, leads_on)
        protocolo_detectado = None        # "BIN" ou "CSV"

        while not _evento_parar.is_set():

            # ── 2ª linha de defesa: fallback por timeout (4s) ────────────────
            if not _conectar_fallback_enviado and (time.time() - _tempo_abertura) > 4.0:
                _conectar_fallback_enviado = True
                if status.estado_placa == "DESCONECTADO":
                    _debug("Fallback timeout 4s — enviando #CONECTAR (handshake reativo não ocorreu)")
                    _enviar_comando("#CONECTAR")

            # ── Leitura bufferizada ───────────────────────────────────────────
            waiting = ser.in_waiting
            if waiting > 0:
                chunk = ser.read(min(waiting, 4096))
                byte_buffer.extend(chunk)
            elif len(byte_buffer) == 0:
                # Só dorme se não há dados novos E buffer está vazio.
                time.sleep(0.002)
                continue

            # ── Processar o buffer ────────────────────────────────────────────
            while len(byte_buffer) > 0:
                b = byte_buffer[0]

                # ── PACOTE BINÁRIO (ECG data) ─────────────────────────────────
                if b == SYNC_BYTE:
                    if len(byte_buffer) < PKT_SIZE:
                        break  # espera mais bytes chegarem

                    pkt         = byte_buffer[1:5]
                    byte_buffer = byte_buffer[5:]

                    flags_seq = pkt[0]
                    adc_hi    = pkt[1]
                    adc_lo    = pkt[2]
                    checksum  = pkt[3]

                    if (flags_seq ^ adc_hi ^ adc_lo) != checksum:
                        status.amostras_perdidas += 1
                        if protocolo_detectado is None:
                            protocolo_detectado = "BIN"
                            _debug("Protocolo detectado: BINÁRIO (checksum falhou neste pacote)")
                        continue

                    if protocolo_detectado is None:
                        protocolo_detectado = "BIN"
                        _debug("Protocolo detectado: BINÁRIO (250 Hz direto)")

                    leads_on = bool(flags_seq & 0x80)
                    adc_raw  = struct.unpack('>h', bytes([adc_hi, adc_lo]))[0]

                    status.amostras_raw       += 1
                    status.amostras_recebidas += 1   # [v3.4] sem decimação: raw == recebidas

                    lote_amostras.append((adc_raw, leads_on))

                    # Publica o lote a cada 25 amostras (100 ms)
                    if len(lote_amostras) >= CHUNK_SIZE:
                        agora_us = int(time.time() * 1_000_000)
                        for i, (adc_val, lo) in enumerate(lote_amostras):
                            amostra = AmostraECG(
                                timestamp_us=agora_us + int(i * 4000),  # Δt = 4000 µs (250 Hz)
                                adc_raw=adc_val,
                                # [FIX v3.4] Polaridade corrigida: sinal negado para
                                # compensar inversão na saída diferencial do AD8232.
                                tensao_mv=-(adc_val * ADC_MV_PER_LSB),
                                leads_on=lo,
                                idx=idx,
                            )
                            idx += 1
                            broadcaster.publicar(amostra)
                        lote_amostras.clear()

                # ── TEXTO (comandos, status, CSV) ─────────────────────────────
                else:
                    newline_pos = byte_buffer.find(b'\n')
                    if newline_pos == -1:
                        if len(byte_buffer) > 512:
                            byte_buffer.clear()  # overflow protection
                        break  # espera mais bytes para completar a linha

                    line_bytes  = byte_buffer[:newline_pos]
                    byte_buffer = byte_buffer[newline_pos + 1:]

                    try:
                        linha = line_bytes.decode("utf-8", errors="replace").strip()
                    except Exception:
                        continue

                    if not linha:
                        continue

                    # Marcador/status do firmware
                    if (linha.startswith("#") or
                            linha.startswith("Pronto") or
                            linha.startswith("Inicializando") or
                            linha.startswith("ERRO") or
                            linha.startswith("timestamp")):
                        _processar_linha_texto(linha)
                        continue

                    # Fallback: CSV (firmware v2)
                    csv_result = _parsear_csv(linha)
                    if csv_result is not None:
                        adc_raw, leads_on = csv_result

                        if protocolo_detectado is None:
                            protocolo_detectado = "CSV"
                            _debug("Protocolo detectado: CSV (firmware v2)")

                        status.amostras_raw       += 1
                        status.amostras_recebidas += 1

                        # No modo CSV, publica diretamente (já está a 250 Hz)
                        amostra = AmostraECG(
                            timestamp_us=int(time.time() * 1_000_000),
                            adc_raw=adc_raw,
                            # [FIX v3.4] Polaridade corrigida também no fallback CSV
                            tensao_mv=-(adc_raw * ADC_MV_PER_LSB),
                            leads_on=leads_on,
                            idx=idx,
                        )
                        idx += 1
                        broadcaster.publicar(amostra)
                    else:
                        _debug(f"IGNORADO: {linha[:80]}")

        # Flush: publica amostras restantes no lote ao encerrar sessão
        if lote_amostras:
            agora_us = int(time.time() * 1_000_000)
            for i, (adc_val, lo) in enumerate(lote_amostras):
                amostra = AmostraECG(
                    timestamp_us=agora_us + int(i * 4000),
                    adc_raw=adc_val,
                    tensao_mv=-(adc_val * ADC_MV_PER_LSB),
                    leads_on=lo,
                    idx=idx,
                )
                idx += 1
                broadcaster.publicar(amostra)

        _enviar_comando("#DESCONECTAR")
        time.sleep(0.15)
        ser.close()
        _debug("Porta fechada")

    except serial.SerialException as e:
        status.erro = f"Não foi possível abrir {porta}: {e}"
        _debug(f"ERRO SERIAL: {e}")
    finally:
        _serial_conn        = None
        status.conectado    = False
        status.sessao_ativa = False
        status.estado_placa = "DESCONECTADO"


# ─── API pública ──────────────────────────────────────────────────────────────

def conectar(porta: str, baud: int = 115200) -> bool:
    global _thread_leitura
    if status.conectado:
        return True
    _evento_parar.clear()
    _thread_leitura = threading.Thread(
        target=_loop_leitura, args=(porta, baud),
        daemon=True, name="SerialReader"
    )
    _thread_leitura.start()
    for _ in range(30):
        time.sleep(0.1)
        if status.conectado or status.erro:
            break
    return status.conectado


def desconectar():
    _evento_parar.set()
    if _thread_leitura and _thread_leitura.is_alive():
        _thread_leitura.join(timeout=3)
    status.estado_placa = "DESCONECTADO"


def iniciar_leitura() -> dict:
    if not status.conectado:
        return {"erro": "Serial não conectado"}
    if status.estado_placa not in ("CONECTADO_IDLE",):
        return {"erro": f"Estado inválido para iniciar: {status.estado_placa}"}
    _enviar_comando("#INICIAR")
    return {"ok": True}


def parar_leitura() -> dict:
    if not status.conectado:
        return {"erro": "Serial não conectado"}
    _enviar_comando("#PARAR")
    return {"ok": True}


def obter_status() -> dict:
    return {
        "conectado":          status.conectado,
        "porta":              status.porta,
        "amostras_recebidas": status.amostras_recebidas,
        "amostras_raw":       status.amostras_raw,       # igual a amostras_recebidas em v3.4
        "amostras_perdidas":  status.amostras_perdidas,
        "sessao_ativa":       status.sessao_ativa,
        "estado_placa":       status.estado_placa,
        "fs_raw":             status.fs_raw,
        "fs_out":             status.fs_out,
        "erro":               status.erro,
        "debug_log":          obter_debug_log(),
    }