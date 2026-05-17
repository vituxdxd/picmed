# =============================================================================
# CAMADA 3 — PROCESSAMENTO VFC
# Extrai intervalos R-R do sinal ECG bruto usando NeuroKit2,
# calcula métricas de VFC (SDNN, RMSSD, pNN50) e o Índice de Estresse
# de Baevsky (SI), que não é fornecido nativamente pelo NeuroKit2.
# =============================================================================

import numpy as np
import neurokit2 as nk
from typing import Optional

 
# ─── Constantes ───────────────────────────────────────────────────────────────
TAXA_AMOSTRAGEM = 250       # Hz — deve coincidir com a configuração do ADS1115
BIN_BAEVSKY_S   = 0.050     # 50 ms — tamanho do bin do histograma para cálculo do SI


# ─── Índice de Estresse de Baevsky (SI) ───────────────────────────────────────
def calcular_baevsky(intervalos_rr_s: np.ndarray) -> dict:
    """
    Calcula o Índice de Estresse de Baevsky (Stress Index — SI).

    Fórmula:
        SI = (AMo × 100) / (2 × Mo × mxDMn)

    Onde (todos os valores em segundos):
        Mo    = valor central do bin mais frequente no histograma de R-R
        AMo   = fração de R-Rs que caem no bin do Mo (0.0 a 1.0)
        mxDMn = RR_max − RR_min (variação total dos intervalos)

    Referência de interpretação (Baevsky et al., 2001 / Huizinga et al., 2025):
        SI < 50     → tônus vagal dominante (relaxamento / atletas)
        50 ≤ SI < 150 → equilíbrio autonômico (normal)
        SI ≥ 150    → dominância simpática (estresse / risco oculto)

    Parâmetros
    ----------
    intervalos_rr_s : np.ndarray
        Array de intervalos R-R em SEGUNDOS.

    Retorna
    -------
    dict com: mo_ms, amo_pct, mxdmn_ms, si_baevsky
    """
    rr = np.array(intervalos_rr_s, dtype=float)

    # Garante que o array tem dados suficientes para o histograma
    if len(rr) < 10:
        return {"mo_ms": None, "amo_pct": None, "mxdmn_ms": None, "si_baevsky": None}

    # Histograma com bins de 50 ms
    rr_min, rr_max = rr.min(), rr.max()
    mxdmn = rr_max - rr_min

    if mxdmn < 1e-6:
        # Todos os R-Rs são idênticos — ritmo completamente rígido
        # SI seria infinito matematicamente; retorna valor sentinela alto.
        mo = rr.mean()
        return {
            "mo_ms": round(mo * 1000, 2),
            "amo_pct": 100.0,
            "mxdmn_ms": 0.0,
            "si_baevsky": 9999.0
        }

    bins = np.arange(rr_min, rr_max + BIN_BAEVSKY_S, BIN_BAEVSKY_S)
    hist, bin_edges = np.histogram(rr, bins=bins)

    # Mo: centro do bin com maior contagem
    idx_max = int(np.argmax(hist))
    mo = (bin_edges[idx_max] + bin_edges[idx_max + 1]) / 2

    # AMo: fração das amostras no bin dominante
    amo = hist[idx_max] / len(rr)   # 0.0 – 1.0

    # SI
    si = (amo * 100) / (2 * mo * mxdmn)

    return {
        "mo_ms":      round(mo * 1000, 2),
        "amo_pct":    round(amo * 100, 2),
        "mxdmn_ms":  round(mxdmn * 1000, 2),
        "si_baevsky": round(si, 2),
    }


# ─── Processamento completo de uma sessão ────────────────────────────────────
def processar_sessao(amostras_ecg: list[dict]) -> Optional[dict]:
    """
    Recebe a lista de amostras ECG brutas (dicts com tensao_mv)
    e retorna todas as métricas de VFC calculadas.

    Retorna None se os dados forem insuficientes ou inválidos.
    """
    if len(amostras_ecg) < TAXA_AMOSTRAGEM * 30:
        # Menos de 30 segundos de dados — insuficiente para VFC confiável
        return {"erro": f"Dados insuficientes: {len(amostras_ecg)} amostras (mínimo: {TAXA_AMOSTRAGEM * 30})"}

    # ── 1. Sinal bruto ────────────────────────────────────────────────────
    sinal = np.array([a["tensao_mv"] for a in amostras_ecg], dtype=float)

    # ── 2. Processamento ECG com NeuroKit2 ───────────────────────────────
    # nk.ecg_process detecta picos R, limpa o sinal e valida os picos.
    # Retorna um DataFrame com anotações e um dict de resultados.
    try:
        sinais, info = nk.ecg_process(sinal, sampling_rate=TAXA_AMOSTRAGEM)
    except Exception as e:
        return {"erro": f"Falha no processamento NeuroKit2: {str(e)}"}

    # ── 3. Extrai picos R validados ───────────────────────────────────────
    picos_r = info.get("ECG_R_Peaks", [])
    if len(picos_r) < 10:
        return {"erro": f"Poucos picos R detectados: {len(picos_r)}"}

    # ── 4. Intervalos R-R ─────────────────────────────────────────────────
    # Diferença entre índices de picos consecutivos → converte para segundos
    rr_samples = np.diff(picos_r)
    rr_s = rr_samples / TAXA_AMOSTRAGEM

    # Filtra R-Rs fisiologicamente plausíveis: 300 ms – 2000 ms (30–200 bpm)
    mascara = (rr_s >= 0.30) & (rr_s <= 2.00)
    rr_validos = rr_s[mascara]

    if len(rr_validos) < 5:
        return {"erro": "Poucos intervalos R-R válidos após filtragem fisiológica."}

    # ── 5. Métricas temporais (domínio do tempo) ──────────────────────────
    rr_ms = rr_validos * 1000  # converte para ms para as métricas convencionais

    sdnn  = float(np.std(rr_ms, ddof=1))
    rmssd = float(np.sqrt(np.mean(np.diff(rr_ms) ** 2)))
    nn50  = int(np.sum(np.abs(np.diff(rr_ms)) > 50))
    pnn50 = round(nn50 / (len(rr_ms) - 1) * 100, 2) if len(rr_ms) > 1 else 0.0

    # Frequência cardíaca média
    fc_media = round(60.0 / np.mean(rr_validos), 1)

    # ── 6. Índice de Baevsky ──────────────────────────────────────────────
    baevsky = calcular_baevsky(rr_validos)

    # ── 7. Interpretação clínica do SI ───────────────────────────────────
    si = baevsky.get("si_baevsky")
    if si is None:
        classificacao_si = "indisponivel"
    elif si < 50:
        classificacao_si = "vagal"        # predominância parassimpática
    elif si < 150:
        classificacao_si = "equilibrado"  # normal
    else:
        classificacao_si = "simpatico"    # estresse / risco oculto

    return {
        # Métricas temporais
        "sdnn_ms":     round(sdnn, 2),
        "rmssd_ms":    round(rmssd, 2),
        "nn50":        nn50,
        "pnn50":       pnn50,
        "fc_media":    fc_media,
        "n_rr_validos": len(rr_validos),
        # Baevsky
        "mo_ms":       baevsky["mo_ms"],
        "amo_pct":     baevsky["amo_pct"],
        "mxdmn_ms":    baevsky["mxdmn_ms"],
        "si_baevsky":  baevsky["si_baevsky"],
        # Interpretação
        "classificacao_si": classificacao_si,
    }


# ─── BPM em tempo real (NeuroKit2) ───────────────────────────────────────────
def _rr_validos_neurokit(sinal: np.ndarray, fs: int) -> np.ndarray:
    """Extrai intervalos R-R válidos (s) a partir de um trecho de ECG."""
    sinal_limpo = nk.ecg_clean(sinal, sampling_rate=fs, method="neurokit")
    _, info = nk.ecg_peaks(sinal_limpo, sampling_rate=fs, method="neurokit")

    picos_r = np.asarray(info.get("ECG_R_Peaks", []), dtype=int)
    if len(picos_r) < 3:
        return np.array([], dtype=float)

    rr_s = np.diff(picos_r) / fs
    # Faixa de FC plausível para monitorização em repouso/estresse: 40–180 bpm
    mascara = (rr_s >= (60.0 / 180.0)) & (rr_s <= (60.0 / 40.0))
    return rr_s[mascara]


def estimar_bpm_tempo_real(sinal_mv: list[float] | np.ndarray, fs: int = TAXA_AMOSTRAGEM) -> dict:
    """
    Estima BPM de forma robusta em janela deslizante usando NeuroKit2.

    Estratégia:
    1) testa polaridade normal e invertida;
    2) escolhe a que gerar mais RR válidos;
    3) calcula BPM por mediana dos últimos RRs.
    """
    sinal = np.asarray(sinal_mv, dtype=float)

    janela_min = int(fs * 4)  # mínimo de 4 s para estabilizar detecção
    if len(sinal) < janela_min:
        return {
            "bpm": None,
            "erro": f"Dados insuficientes para BPM: {len(sinal)} amostras (mínimo: {janela_min})",
        }

    try:
        rr_pos = _rr_validos_neurokit(sinal, fs)
        rr_neg = _rr_validos_neurokit(-sinal, fs)
    except Exception as e:
        return {"bpm": None, "erro": f"Falha no NeuroKit2 (tempo real): {str(e)}"}

    if len(rr_pos) >= len(rr_neg):
        rr = rr_pos
        polaridade = "normal"
    else:
        rr = rr_neg
        polaridade = "invertida"

    if len(rr) < 2:
        return {"bpm": None, "erro": "Poucos intervalos RR válidos na janela."}

    rr_recentes = rr[-6:]  # ~últimos batimentos para reduzir oscilação rápida
    bpm_inst = 60.0 / rr_recentes
    bpm_inst = bpm_inst[(bpm_inst >= 40.0) & (bpm_inst <= 180.0)]

    if len(bpm_inst) == 0:
        return {"bpm": None, "erro": "BPM instantâneo fora da faixa fisiológica."}

    bpm_medido = float(np.median(bpm_inst))
    variacao = float(np.std(bpm_inst)) if len(bpm_inst) > 1 else 0.0

    return {
        "bpm": round(bpm_medido, 1),
        "rr_validos": int(len(rr)),
        "janela_s": round(len(sinal) / fs, 1),
        "variacao_bpm": round(variacao, 1),
        "polaridade_detectada": polaridade,
    }


# ─── STAI-S-6 e STAI-T-6 (Fioravanti-Bastos et al., 2011) ──────────────────
def calcular_stai(respostas: dict) -> dict:
    """
    Calcula os escores do STAI-S-6 (estado) e STAI-T-6 (traço).

    Versão curta oficial validada em português brasileiro.
    Cada escala tem 6 itens: 3 de ansiedade-presente + 3 de ansiedade-ausente.

    STAI-S-6 (Estado) — "Como você se sente agora, neste momento?" (1-4 Likert)
      Itens ansiedade-presente (não inverter):
        s17 "Estou preocupado(a)"
        s3  "Estou tenso(a)"
        s12 "Sinto-me nervoso(a)"
      Itens ansiedade-ausente (INVERTER: 4->1, 3->2, 2->3, 1->4):
        s1  "Sinto-me calmo(a)"
        s15 "Estou descontraído(a)"
        s5  "Sinto-me à vontade"

    STAI-T-6 (Traço) — "Como você geralmente se sente?" (1-4 Likert)
      Itens ansiedade-presente (não inverter):
        t9  "Preocupo-me demais com coisas sem importância"
        t21 "Sinto-me nervoso(a) e inquieto(a)"
        t20 "Fico tenso(a) e perturbado(a) quando penso em meus problemas no momento"
      Itens ansiedade-ausente (INVERTER: 4->1, 3->2, 2->3, 1->4):
        t13 "Sinto-me uma pessoa segura"
        t7  "Sou calmo(a), ponderado(a) e senhor(a) de mim mesmo(a)"
        t25 "Tomo decisões facilmente"

    Retorna
    -------
    dict com score_s (6-24), score_t (6-24), classificacao_s, classificacao_t.
    """
    def _inv(valor: int) -> int:
        """Inverte item de ansiedade-ausente: 4→1, 3→2, 2→3, 1→4."""
        return 5 - valor

    # ── STAI-S-6 ──────────────────────────────────────────────────────────
    s_present = [
        int(respostas.get("s17", 1) or 1),  # preocupado
        int(respostas.get("s3", 1) or 1),    # tenso
        int(respostas.get("s12", 1) or 1),   # nervoso
    ]
    s_absent = [
        _inv(int(respostas.get("s1", 1) or 1)),   # calmo (inv)
        _inv(int(respostas.get("s15", 1) or 1)),  # descontraído (inv)
        _inv(int(respostas.get("s5", 1) or 1)),   # à vontade (inv)
    ]
    score_s = sum(s_present) + sum(s_absent)  # 6–24

    # ── STAI-T-6 ──────────────────────────────────────────────────────────
    t_present = [
        int(respostas.get("t9", 1) or 1),   # preocupa demais
        int(respostas.get("t21", 1) or 1),  # nervoso inquieto
        int(respostas.get("t20", 1) or 1),  # tenso com problemas
    ]
    t_absent = [
        _inv(int(respostas.get("t13", 1) or 1)),  # seguro (inv)
        _inv(int(respostas.get("t7", 1) or 1)),    # calmo ponderado (inv)
        _inv(int(respostas.get("t25", 1) or 1)),   # decisões fácil (inv)
    ]
    score_t = sum(t_present) + sum(t_absent)  # 6–24

    # ── Classificação ─────────────────────────────────────────────────────
    # Baseado em médias brasileiras do estudo de validação
    # (Fioravanti-Bastos et al., 2011, n≈4000): média ~12-14, DP ~3-4
    def _classificar_stai(score: int, escala: str) -> str:
        if score <= 9:
            return "baixo"
        elif score <= 15:
            return "medio"
        else:
            return "alto"

    return {
        "score_s": score_s,
        "score_t": score_t,
        "classificacao_s": _classificar_stai(score_s, "s"),
        "classificacao_t": _classificar_stai(score_t, "t"),
        "s_present": s_present,
        "s_absent_raw": [int(respostas.get("s1",1)or 1), int(respostas.get("s15",1)or 1), int(respostas.get("s5",1)or 1)],
        "t_present": t_present,
        "t_absent_raw": [int(respostas.get("t13",1)or 1), int(respostas.get("t7",1)or 1), int(respostas.get("t25",1)or 1)],
    }


# ─── IPAQ: Questionário Internacional de Atividade Física (versão curta) ─────
def calcular_ipaq(respostas: dict) -> dict:
    """
    Calcula os MET-minutos/semana e classificação do IPAQ versão curta.

    Parâmetros
    ----------
    respostas : dict
        Chaves esperadas:
        - q1_vigorosa_dias       : int — dias/semana de atividade vigorosa
        - q2_vigorosa_min        : int — minutos/dia de atividade vigorosa
        - q3_moderada_dias       : int — dias/semana de atividade moderada
        - q4_moderada_min        : int — minutos/dia de atividade moderada
        - q5_caminhada_dias      : int — dias/semana de caminhada
        - q6_caminhada_min       : int — minutos/dia de caminhada
        - q7_sentado_horas       : int — horas sentado/dia de semana
        - q7_sentado_min         : int — minutos sentado/dia de semana

    Classificação adaptada (4 categorias):
      - Sedentário
      - Insuficientemente ativo
      - Ativo
      - Muito ativo

    Retorna
    -------
    dict com met_min_semana por categoria, total, e classificacao.
    """
    # Extrai valores brutos
    vig_dias = int(respostas.get("q1_vigorosa_dias", 0) or 0)
    vig_min  = int(respostas.get("q2_vigorosa_min", 0) or 0)
    mod_dias = int(respostas.get("q3_moderada_dias", 0) or 0)
    mod_min  = int(respostas.get("q4_moderada_min", 0) or 0)
    cam_dias = int(respostas.get("q5_caminhada_dias", 0) or 0)
    cam_min  = int(respostas.get("q6_caminhada_min", 0) or 0)
    sent_horas = int(respostas.get("q7_sentado_horas", 0) or 0)
    sent_min   = int(respostas.get("q7_sentado_min", 0) or 0)

    # MET-min/semana = MET × minutos × dias
    met_vigorosa = round(8.0 * vig_min * vig_dias, 1)
    met_moderada = round(4.0 * mod_min * mod_dias, 1)
    met_caminhada = round(3.3 * cam_min * cam_dias, 1)
    met_total = round(met_vigorosa + met_moderada + met_caminhada, 1)

    # Dias ativos totais
    dias_ativos = vig_dias + mod_dias + cam_dias

    # Classificação (4 categorias)
    if met_total == 0:
        classificacao = "sedentario"
    elif met_total < 600:
        classificacao = "insuficiente"
    elif met_total < 1500:
        classificacao = "ativo"
    else:
        classificacao = "muito_ativo"

    # Tempo sentado total em minutos
    sentado_total_min = sent_horas * 60 + sent_min

    return {
        "met_vigorosa": met_vigorosa,
        "met_moderada": met_moderada,
        "met_caminhada": met_caminhada,
        "met_total": met_total,
        "dias_ativos_total": dias_ativos,
        "classificacao": classificacao,
        "sentado_total_min": sentado_total_min,
        "dados_brutos": {
            "vigorosa_dias": vig_dias, "vigorosa_min": vig_min,
            "moderada_dias": mod_dias, "moderada_min": mod_min,
            "caminhada_dias": cam_dias, "caminhada_min": cam_min,
            "sentado_horas": sent_horas, "sentado_min": sent_min,
        }
    }


# ─── PSQI: Índice de Qualidade do Sono de Pittsburgh ─────────────────────────
def calcular_psqi(respostas: dict) -> dict:
    """
    Calcula os 7 componentes e o escore global do PSQI a partir das
    respostas brutas do questionário.

    Parâmetros
    ----------
    respostas : dict
        Deve conter as seguintes chaves (valores brutos conforme o questionário):
        - q1_hora_deitar      : str "HH:MM" — hora usual de deitar
        - q2_min_adormecer    : int — minutos para adormecer
        - q3_hora_levantar    : str "HH:MM" — hora usual de levantar
        - q4_horas_sono       : float — horas de sono por noite
        - q5a .. q5j          : int 0-3 — frequência de cada distúrbio
        - q6_qualidade        : int 0-3 — qualidade subjetiva do sono
        - q7_medicamento      : int 0-3 — uso de remédio para dormir
        - q8_ficar_acordado   : int 0-3 — dificuldade ficar acordado
        - q9_entusiasmo       : int 0-3 — problema com entusiasmo/ânimo

    Retorna
    -------
    dict com componente_1..7, escore_global (0-21) e classificacao.
    """
    def _parse_hora(hora_str: str) -> float:
        """Converte 'HH:MM' para horas decimais (ex: '23:30' -> 23.5)."""
        try:
            h, m = hora_str.strip().split(":")
            return float(h) + float(m) / 60.0
        except Exception:
            return 0.0

    # ── Componente 1: Qualidade subjetiva do sono (Q6) ──────────────────
    q6 = int(respostas.get("q6_qualidade", 0))
    c1 = q6  # 0-3

    # ── Componente 2: Latência do sono (Q2 + Q5a) ───────────────────────
    q2 = float(respostas.get("q2_min_adormecer", 0))
    if q2 <= 15:
        score_q2 = 0
    elif q2 <= 30:
        score_q2 = 1
    elif q2 <= 60:
        score_q2 = 2
    else:
        score_q2 = 3

    q5a = int(respostas.get("q5a", 0))
    soma_c2 = score_q2 + q5a
    if soma_c2 == 0:
        c2 = 0
    elif soma_c2 <= 2:
        c2 = 1
    elif soma_c2 <= 4:
        c2 = 2
    else:
        c2 = 3

    # ── Componente 3: Duração do sono (Q4) ──────────────────────────────
    q4 = float(respostas.get("q4_horas_sono", 0))
    if q4 > 7:
        c3 = 0
    elif q4 >= 6:
        c3 = 1
    elif q4 >= 5:
        c3 = 2
    else:
        c3 = 3

    # ── Componente 4: Eficiência habitual do sono ───────────────────────
    hora_deitar = _parse_hora(respostas.get("q1_hora_deitar", "00:00"))
    hora_levantar = _parse_hora(respostas.get("q3_hora_levantar", "00:00"))
    # Corrige virada de dia: se levantar < deitar, soma 24h
    if hora_levantar < hora_deitar:
        horas_no_leito = (hora_levantar + 24.0) - hora_deitar
    else:
        horas_no_leito = hora_levantar - hora_deitar

    if horas_no_leito > 0:
        eficiencia = (q4 / horas_no_leito) * 100.0
    else:
        eficiencia = 0.0

    if eficiencia > 85:
        c4 = 0
    elif eficiencia >= 75:
        c4 = 1
    elif eficiencia >= 65:
        c4 = 2
    else:
        c4 = 3

    # ── Componente 5: Distúrbios do sono (Q5b a Q5j) ────────────────────
    soma_disturbios = 0
    for item in ["q5b", "q5c", "q5d", "q5e", "q5f", "q5g", "q5h", "q5i", "q5j"]:
        soma_disturbios += int(respostas.get(item, 0))
    if soma_disturbios == 0:
        c5 = 0
    elif soma_disturbios <= 9:
        c5 = 1
    elif soma_disturbios <= 18:
        c5 = 2
    else:
        c5 = 3

    # ── Componente 6: Uso de remédio para dormir (Q7) ───────────────────
    q7 = int(respostas.get("q7_medicamento", 0))
    c6 = q7  # 0-3

    # ── Componente 7: Disfunção diurna (Q8 + Q9) ────────────────────────
    q8 = int(respostas.get("q8_ficar_acordado", 0))
    q9 = int(respostas.get("q9_entusiasmo", 0))
    soma_diurna = q8 + q9
    if soma_diurna == 0:
        c7 = 0
    elif soma_diurna <= 2:
        c7 = 1
    elif soma_diurna <= 4:
        c7 = 2
    else:
        c7 = 3

    escore_global = c1 + c2 + c3 + c4 + c5 + c6 + c7

    if escore_global <= 5:
        classificacao = "boa"
    elif escore_global <= 10:
        classificacao = "baixa"
    elif escore_global <= 15:
        classificacao = "ruim"
    else:
        classificacao = "muito_ruim"

    return {
        "componente_1": c1,
        "componente_2": c2,
        "componente_3": c3,
        "componente_4": c4,
        "componente_5": c5,
        "componente_6": c6,
        "componente_7": c7,
        "escore_global": escore_global,
        "classificacao": classificacao,
        "eficiencia_sono_pct": round(eficiencia, 1),
        "horas_no_leito": round(horas_no_leito, 1),
    }


# ─── Interpretação do Índice de Baevsky ───────────────────────────────────────
def interpretar_si(si: Optional[float]) -> dict:
    """
    Retorna rótulo, cor e descrição clínica para o SI de Baevsky.
    Usado pela interface para colorir os badges de resultado.
    """
    if si is None:
        return {"rotulo": "N/D", "cor": "#6b7280", "descricao": "Não disponível"}
    if si < 50:
        return {"rotulo": "Predomínio Parassimpático", "cor": "#3b82f6",
                "descricao": "Predominância parassimpática. Comum em atletas e estados de relaxamento profundo."}
    if si < 150:
        return {"rotulo": "Normotonia", "cor": "#22c55e",
                "descricao": "Modulação autonômica dentro da faixa normal."}
    if si < 300:
        return {"rotulo": "Predomínio Simpático Moderado", "cor": "#eab308",
                "descricao": "Dominância simpática moderada. Possível estresse fisiológico."}
    return {"rotulo": "Simpaticotonia Acentuada", "cor": "#ef4444",
            "descricao": "Dominância simpática acentuada. Candidato ao grupo de risco 'Oculto' (PSS-10 vs SI)."}