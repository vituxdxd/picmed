# =============================================================================
# CAMADA 4 — SERVIDOR FLASK (v3)
# Orquestra as camadas 1–3, serve a interface web e expõe os endpoints
# REST + SSE para comunicação em tempo real com o frontend.
#
# MUDANÇAS v3:
#   • Rotas separadas para leitura hardware (#INICIAR/#PARAR) vs sessão banco
#   • Rota /api/leitura/iniciar e /api/leitura/parar para controle do ESP32
#   • Sessão de banco abre automaticamente ao iniciar leitura (se participante selecionado)
#   • Status inclui campos novos: amostras_raw, fs_raw, fs_out
#   • Fix: render_template path corrigido (era "templates/index.html")
#   • Compatível com protocolo binário (decimação 860→250 Hz na camada 1)
# =============================================================================

import json
import time
import threading
import statistics
from collections import deque
from flask import Flask, render_template, request, jsonify, Response, stream_with_context

import camada1_aquisicao as aquisicao
import camada2_banco     as banco
import camada3_processamento as processamento

# ─── Inicialização ────────────────────────────────────────────────────────────
app = Flask(__name__)
banco.inicializar()

# Estado da sessão ativa (compartilhado entre threads)
_sessao_atual = {
    "id_sessao":       None,
    "id_participante": None,
    "ativa":           False,
    "total_amostras":  0,
    "duracao_seg":     300,      # [NOVO] duração configurável
    "total_alvo":      75000,    # [NOVO] 250 Hz × duracao_seg
}
_lock_sessao = threading.Lock()

# Estado de BPM em tempo real (janela deslizante + suavização)
_BPM_FS = processamento.TAXA_AMOSTRAGEM
_BPM_JANELA_S = 10
_buffer_bpm_mv = deque(maxlen=_BPM_FS * _BPM_JANELA_S)
_lock_bpm = threading.Lock()
_BPM_HIST_RAW = deque(maxlen=8)
_BPM_EMA_ALPHA = 0.18
_BPM_SLEW_BPM_POR_S = 1.5
_bpm_state = {
    "filtrado": None,
    "ultimo_ts": 0.0,
    "ultimo_update": 0.0,
}


def _resetar_estado_bpm():
    """Limpa buffer e estado do BPM em tempo real."""
    with _lock_bpm:
        _buffer_bpm_mv.clear()
        _BPM_HIST_RAW.clear()
        _bpm_state["filtrado"] = None
        _bpm_state["ultimo_ts"] = 0.0
        _bpm_state["ultimo_update"] = 0.0

# ─── Thread de persistência das amostras ────────────────────────────────────
_fila_persistencia = aquisicao.broadcaster.registrar(maxsize=2000)

def _worker_persistencia():
    """Loop em thread separada: drena a fila e bufferiza no banco."""
    while True:
        try:
            amostra = _fila_persistencia.get(timeout=1)

            # Buffer de BPM em tempo real (somente com eletrodos válidos)
            if amostra.leads_on:
                with _lock_bpm:
                    _buffer_bpm_mv.append(amostra.tensao_mv)
                    _bpm_state["ultimo_ts"] = time.time()

            with _lock_sessao:
                sid = _sessao_atual["id_sessao"]
                ativa = _sessao_atual["ativa"]

            if sid and ativa:
                banco.bufferizar_amostra(
                    id_sessao=sid,
                    idx=amostra.idx,
                    ts_us=amostra.timestamp_us,
                    adc_raw=amostra.adc_raw,
                    tensao_mv=amostra.tensao_mv,
                    leads_on=amostra.leads_on,
                )
                with _lock_sessao:
                    _sessao_atual["total_amostras"] += 1

                # Auto-encerra ao atingir o total alvo de amostras
                with _lock_sessao:
                    alvo = _sessao_atual["total_alvo"]
                if _sessao_atual["total_amostras"] >= alvo:
                    _encerrar_sessao_interna()
                    aquisicao.parar_leitura()  # envia #PARAR ao ESP32

        except Exception:
            pass

threading.Thread(target=_worker_persistencia, daemon=True, name="Persistencia").start()


def _encerrar_sessao_interna():
    """Encerra a sessão ativa no banco."""
    with _lock_sessao:
        if not _sessao_atual["ativa"]:
            return
        sid = _sessao_atual["id_sessao"]
        total = _sessao_atual["total_amostras"]
        _sessao_atual["ativa"] = False

    banco.flush_final()
    banco.fechar_sessao(sid, total)


# ─── Callback de #FIM_SESSAO ─────────────────────────────────────────────────
# O firmware v3.5 encerra a leitura por tempo (millis) e envia #FIM_SESSAO.
# Registramos após _encerrar_sessao_interna existir para evitar NameError.
# Quando o ESP32 termina a coleta, este callback fecha a sessão no banco
# imediatamente, sem depender do contador exato de amostras (que pode divergir
# por drift de clock entre ESP32 e Python).
aquisicao.on_fim_sessao = _encerrar_sessao_interna


# ═══════════════════════════════════════════════════════════════════════════════
# ROTAS — INTERFACE
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    # [FIX v3] Corrigido: era "templates/index.html", Flask já procura em templates/
    return render_template("index.html")


# ═══════════════════════════════════════════════════════════════════════════════
# ROTAS — CAMADA 1: AQUISIÇÃO (SERIAL + HARDWARE)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/portas")
def api_portas():
    """Lista as portas seriais disponíveis no sistema."""
    return jsonify(aquisicao.listar_portas())


@app.route("/api/conectar", methods=["POST"])
def api_conectar():
    """Abre a conexão serial com o ESP32 (envia #CONECTAR)."""
    dados = request.json or {}
    porta = dados.get("porta", "")
    if not porta:
        return jsonify({"erro": "Porta não informada"}), 400

    ok = aquisicao.conectar(porta)
    if not ok:
        return jsonify({"erro": aquisicao.status.erro or "Falha ao conectar"}), 500

    return jsonify({"ok": True, "porta": porta})


@app.route("/api/desconectar", methods=["POST"])
def api_desconectar():
    """Encerra a conexão serial (envia #DESCONECTAR)."""
    # Se houver sessão ativa, encerra primeiro
    _encerrar_sessao_interna()
    aquisicao.desconectar()
    _resetar_estado_bpm()
    return jsonify({"ok": True})


@app.route("/api/leitura/iniciar", methods=["POST"])
def api_iniciar_leitura():
    """
    [NOVO v3] Envia #INICIAR ao ESP32 para iniciar a leitura ECG.
    Se id_participante fornecido, abre sessão no banco automaticamente.
    Separado de api_iniciar_sessao para permitir controle granular.
    """
    if not aquisicao.status.conectado:
        return jsonify({"erro": "Serial não conectado"}), 400

    _resetar_estado_bpm()

    dados = request.json or {}
    id_participante = dados.get("id_participante")
    duracao_seg = int(dados.get("duracao_seg", 300))
    total_alvo = duracao_seg * 250  # 250 Hz após decimação

    # Abre sessão no banco se participante informado e não há sessão ativa
    with _lock_sessao:
        if id_participante and not _sessao_atual["ativa"]:
            sid = banco.abrir_sessao(id_participante)
            _sessao_atual.update({
                "id_sessao":       sid,
                "id_participante": id_participante,
                "ativa":           True,
                "total_amostras":  0,
                "duracao_seg":     duracao_seg,
                "total_alvo":      total_alvo,
            })

    # Envia comando ao ESP32
    resultado = aquisicao.iniciar_leitura()
    if "erro" in resultado:
        return jsonify(resultado), 400

    with _lock_sessao:
        sid = _sessao_atual["id_sessao"]

    return jsonify({"ok": True, "id_sessao": sid})


@app.route("/api/leitura/parar", methods=["POST"])
def api_parar_leitura():
    """
    [NOVO v3] Envia #PARAR ao ESP32 para parar a leitura ECG.
    Encerra a sessão no banco se houver uma ativa.
    """
    resultado = aquisicao.parar_leitura()
    _encerrar_sessao_interna()
    _resetar_estado_bpm()
    return jsonify({"ok": True})


@app.route("/api/status_serial")
def api_status_serial():
    """Retorna status completo: serial + sessão + hardware."""
    s = aquisicao.obter_status()
    with _lock_sessao:
        s["id_sessao"]      = _sessao_atual["id_sessao"]
        s["sessao_ativa"]   = _sessao_atual["ativa"]
        s["total_amostras"] = _sessao_atual["total_amostras"]
        s["duracao_seg"]    = _sessao_atual["duracao_seg"]
        s["total_alvo"]     = _sessao_atual["total_alvo"]
        alvo = _sessao_atual["total_alvo"]
        s["progresso_pct"]  = round(_sessao_atual["total_amostras"] / max(alvo, 1) * 100, 1)
    return jsonify(s)


@app.route("/api/bpm_tempo_real")
def api_bpm_tempo_real():
    """Estimativa de BPM em tempo real usando NeuroKit2 em janela deslizante."""
    with _lock_bpm:
        janela = list(_buffer_bpm_mv)
        ultimo_ts = _bpm_state["ultimo_ts"]
        bpm_filtrado = _bpm_state["filtrado"]
        ultimo_update = _bpm_state["ultimo_update"]

    # Se a aquisição parou ou os dados estão antigos, evita exibir valor stale.
    if (time.time() - ultimo_ts) > 2.5:
        return jsonify({"bpm": None, "erro": "Sem dados recentes de ECG."})

    estimativa = processamento.estimar_bpm_tempo_real(janela, fs=_BPM_FS)
    bpm_raw = estimativa.get("bpm")

    if bpm_raw is None:
        return jsonify(estimativa)

    # Janela de mediana temporal dos valores raw para reduzir outliers pontuais.
    with _lock_bpm:
        _BPM_HIST_RAW.append(float(bpm_raw))
        alvo = float(statistics.median(_BPM_HIST_RAW))

    # EMA + limitador de slew-rate para evitar saltos bruscos no display.
    agora = time.time()
    if bpm_filtrado is None:
        bpm_filtrado = float(alvo)
    else:
        dt = max(agora - ultimo_update, 0.2)
        bpm_ema = ((1.0 - _BPM_EMA_ALPHA) * bpm_filtrado) + (_BPM_EMA_ALPHA * float(alvo))
        delta_max = _BPM_SLEW_BPM_POR_S * dt
        delta = bpm_ema - bpm_filtrado
        if delta > delta_max:
            bpm_filtrado = bpm_filtrado + delta_max
        elif delta < -delta_max:
            bpm_filtrado = bpm_filtrado - delta_max
        else:
            bpm_filtrado = bpm_ema

    with _lock_bpm:
        _bpm_state["filtrado"] = bpm_filtrado
        _bpm_state["ultimo_update"] = agora

    resp = dict(estimativa)
    resp["bpm_raw"] = round(float(bpm_raw), 1)
    resp["bpm_alvo"] = round(float(alvo), 1)
    resp["bpm"] = round(float(bpm_filtrado), 1)
    resp["fonte"] = "neurokit2"
    return jsonify(resp)


@app.route("/api/debug/log")
def api_debug_log():
    """[NOVO v3.1] Retorna o ring buffer de debug da camada serial."""
    return jsonify(aquisicao.obter_debug_log())


# ─── SSE: stream de amostras em tempo real ───────────────────────────────────
@app.route("/stream")
def stream_ecg():
    fila_sse = aquisicao.broadcaster.registrar(maxsize=500)

    def gerar():
        lote = []
        ultimo_envio = time.time()

        try:
            while True:
                try:
                    amostra = fila_sse.get(timeout=0.5)
                    lote.append({
                        "idx": amostra.idx,
                        "v":   round(amostra.tensao_mv, 2),
                        "lo":  1 if amostra.leads_on else 0,
                    })
                except Exception:
                    pass

                agora = time.time()
                if lote and (agora - ultimo_envio >= 0.1):
                    payload = json.dumps(lote)
                    yield f"data: {payload}\n\n"
                    lote = []
                    ultimo_envio = agora
                elif agora - ultimo_envio >= 2.0:
                    yield "data: []\n\n"
                    ultimo_envio = agora

        except GeneratorExit:
            pass
        finally:
            aquisicao.broadcaster.desregistrar(fila_sse)

    return Response(
        stream_with_context(gerar()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ROTAS — CAMADA 2: BANCO DE DADOS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/participantes", methods=["GET"])
def api_listar_participantes():
    return jsonify(banco.listar_participantes())


@app.route("/api/participantes", methods=["POST"])
def api_criar_participante():
    d = request.json or {}
    campos_obrigatorios = ["codigo", "ciclo", "idade", "sexo"]
    for campo in campos_obrigatorios:
        if campo not in d:
            return jsonify({"erro": f"Campo obrigatório ausente: {campo}"}), 400
    try:
        pid = banco.criar_participante(d["codigo"], d["ciclo"], int(d["idade"]), d["sexo"])
        return jsonify({"id": pid, "ok": True})
    except Exception as e:
        return jsonify({"erro": str(e)}), 409


@app.route("/api/participante/<int:id_participante>", methods=["DELETE"])
def api_excluir_participante(id_participante: int):
    with _lock_sessao:
        id_ativo = _sessao_atual["id_participante"]
        sessao_ativa_mesmo_participante = (
            _sessao_atual["ativa"]
            and id_ativo is not None
            and int(id_ativo) == id_participante
        )

    if sessao_ativa_mesmo_participante:
        return jsonify({
            "erro": "Pare a leitura/encerre a sessão ativa antes de excluir este participante."
        }), 409

    try:
        # Garante que qualquer lote pendente seja persistido antes da exclusão.
        banco.flush_final()
        resumo = banco.excluir_participante_completo(id_participante)
        if not resumo:
            return jsonify({"erro": "Participante não encontrado."}), 404
        return jsonify({"ok": True, **resumo})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/api/sessoes", methods=["GET"])
def api_listar_sessoes():
    return jsonify(banco.listar_sessoes())


@app.route("/api/sessao/iniciar", methods=["POST"])
def api_iniciar_sessao():
    """Abre uma nova sessão no banco (sem iniciar leitura hardware)."""
    d = request.json or {}
    id_participante = d.get("id_participante")
    if not id_participante:
        return jsonify({"erro": "id_participante é obrigatório"}), 400

    with _lock_sessao:
        if _sessao_atual["ativa"]:
            return jsonify({"erro": "Já existe uma sessão ativa"}), 409

        sid = banco.abrir_sessao(id_participante)
        _sessao_atual.update({
            "id_sessao":       sid,
            "id_participante": id_participante,
            "ativa":           True,
            "total_amostras":  0,
        })

    return jsonify({"id_sessao": sid, "ok": True})


@app.route("/api/sessao/encerrar", methods=["POST"])
def api_encerrar_sessao():
    _encerrar_sessao_interna()
    return jsonify({"ok": True})


@app.route("/api/inventario", methods=["POST"])
def api_salvar_inventario():
    d = request.json or {}
    id_sessao = d.get("id_sessao")
    if not id_sessao:
        return jsonify({"erro": "id_sessao é obrigatório"}), 400
    banco.salvar_inventario(id_sessao, d)
    return jsonify({"ok": True})


@app.route("/api/banco/stats")
def api_banco_stats():
    return jsonify(banco.estatisticas_banco())


# ═══════════════════════════════════════════════════════════════════════════════
# ROTAS — CAMADA 3: PROCESSAMENTO VFC
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/processar/<int:id_sessao>", methods=["POST"])
def api_processar(id_sessao: int):
    amostras = banco.carregar_ecg_sessao(id_sessao)
    if not amostras:
        return jsonify({"erro": "Sessão sem dados ECG."}), 404

    resultado = processamento.processar_sessao(amostras)
    if "erro" in resultado:
        return jsonify(resultado), 422

    banco.salvar_metricas(id_sessao, resultado)
    si = resultado.get("si_baevsky")
    resultado["interpretacao_si"] = processamento.interpretar_si(si)
    return jsonify(resultado)


@app.route("/api/resultado/<int:id_sessao>")
def api_resultado(id_sessao: int):
    metricas = banco.obter_metricas(id_sessao)
    if not metricas:
        return jsonify({"erro": "Sessão não processada."}), 404
    si = metricas.get("si_baevsky")
    metricas["interpretacao_si"] = processamento.interpretar_si(si)
    return jsonify(metricas)


# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import socket
    try:
        host_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        host_ip = "0.0.0.0"
    print("=" * 60)
    print("  PICMED UNICEPLAC — Sistema de Coleta ECG/VFC v3")
    print("  Hardware: ESP32 + AD8232 + ADS1115 @ 860 SPS")
    print("  Protocolo: Binário (decimação 860→250 Hz)")
    print(f"  Local:  http://localhost:5000")
    print(f"  Rede:   http://{host_ip}:5000")
    print("=" * 60)
    app.run(host="0.0.0.0", debug=True, use_reloader=False, threaded=True, port=5000)