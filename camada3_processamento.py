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


# ─── Interpretação do Índice de Baevsky ───────────────────────────────────────
def interpretar_si(si: Optional[float]) -> dict:
    """
    Retorna rótulo, cor e descrição clínica para o SI de Baevsky.
    Usado pela interface para colorir os badges de resultado.
    """
    if si is None:
        return {"rotulo": "N/D", "cor": "#6b7280", "descricao": "Não disponível"}
    if si < 50:
        return {"rotulo": "Vagal", "cor": "#3b82f6",
                "descricao": "Predominância parassimpática. Comum em atletas e estados de relaxamento profundo."}
    if si < 150:
        return {"rotulo": "Equilibrado", "cor": "#22c55e",
                "descricao": "Modulação autonômica dentro da faixa normal."}
    if si < 300:
        return {"rotulo": "Atenção", "cor": "#eab308",
                "descricao": "Dominância simpática moderada. Possível estresse fisiológico."}
    return {"rotulo": "Alto Risco", "cor": "#ef4444",
            "descricao": "Dominância simpática acentuada. Candidato ao grupo de risco 'Oculto' (PSS-10 vs SI)."}