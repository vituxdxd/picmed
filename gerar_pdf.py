# =============================================================================
# GERADOR DE PDF — RELATÓRIO DE RESULTADOS
# Gera um PDF com métricas VFC e questionários, sem identificação do
# participante. Apenas para fins acadêmicos e informativos.
# =============================================================================

from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.platypus.flowables import HRFlowable as HR


# ─── Cores do projeto ────────────────────────────────────────────────────────
AZUL       = HexColor("#2563eb")
TEAL       = HexColor("#0d9488")
VERDE      = HexColor("#16a34a")
AMBER      = HexColor("#d97706")
VERMELHO   = HexColor("#dc2626")
CINZA_TEXT = HexColor("#1e293b")
CINZA_DIM  = HexColor("#64748b")
CINZA_BG   = HexColor("#f8f9fb")
BRANCO     = HexColor("#ffffff")
BORDA      = HexColor("#e2e8f0")


# ─── Estilos ─────────────────────────────────────────────────────────────────
def _criar_estilos():
    estilos = getSampleStyleSheet()

    estilos.add(ParagraphStyle(
        "TituloRelatorio", parent=estilos["Heading1"],
        fontName="Helvetica-Bold", fontSize=18, textColor=AZUL,
        alignment=TA_CENTER, spaceAfter=4*mm,
    ))
    estilos.add(ParagraphStyle(
        "Subtitulo", parent=estilos["Heading2"],
        fontName="Helvetica", fontSize=10, textColor=CINZA_DIM,
        alignment=TA_CENTER, spaceAfter=6*mm,
    ))
    estilos.add(ParagraphStyle(
        "SecaoTitulo", parent=estilos["Heading2"],
        fontName="Helvetica-Bold", fontSize=13, textColor=AZUL,
        spaceBefore=8*mm, spaceAfter=3*mm,
    ))
    estilos.add(ParagraphStyle(
        "MetricaLabel", parent=estilos["Normal"],
        fontName="Helvetica-Bold", fontSize=9, textColor=CINZA_DIM,
        alignment=TA_LEFT,
    ))
    estilos.add(ParagraphStyle(
        "MetricaValor", parent=estilos["Normal"],
        fontName="Helvetica-Bold", fontSize=14, textColor=CINZA_TEXT,
        alignment=TA_CENTER,
    ))
    estilos.add(ParagraphStyle(
        "LegendaMetrica", parent=estilos["Normal"],
        fontName="Helvetica", fontSize=7, textColor=CINZA_DIM,
        alignment=TA_CENTER, leading=9,
    ))
    estilos.add(ParagraphStyle(
        "Disclaimer", parent=estilos["Normal"],
        fontName="Helvetica-Oblique", fontSize=7.5, textColor=CINZA_DIM,
        alignment=TA_CENTER, leading=10,
    ))
    estilos.add(ParagraphStyle(
        "BadgeVerde", parent=estilos["Normal"],
        fontName="Helvetica-Bold", fontSize=8, textColor=VERDE,
        alignment=TA_CENTER,
    ))
    estilos.add(ParagraphStyle(
        "BadgeAmber", parent=estilos["Normal"],
        fontName="Helvetica-Bold", fontSize=8, textColor=AMBER,
        alignment=TA_CENTER,
    ))
    estilos.add(ParagraphStyle(
        "BadgeVermelho", parent=estilos["Normal"],
        fontName="Helvetica-Bold", fontSize=8, textColor=VERMELHO,
        alignment=TA_CENTER,
    ))
    estilos.add(ParagraphStyle(
        "BadgeAzul", parent=estilos["Normal"],
        fontName="Helvetica-Bold", fontSize=8, textColor=AZUL,
        alignment=TA_CENTER,
    ))
    estilos.add(ParagraphStyle(
        "TextoNormal", parent=estilos["Normal"],
        fontName="Helvetica", fontSize=9, textColor=CINZA_TEXT,
        alignment=TA_LEFT, leading=13,
    ))
    return estilos


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _linha():
    return HRFlowable(width="100%", thickness=0.5, color=BORDA,
                       spaceBefore=2*mm, spaceAfter=2*mm)


def _metrica_card(label: str, valor: str, legenda: str, estilos) -> Table:
    """Retorna uma mini-tabela com label, valor e legenda explicativa."""
    lbl = Paragraph(label, estilos["MetricaLabel"])
    val = Paragraph(valor, estilos["MetricaValor"])
    leg = Paragraph(legenda, estilos["LegendaMetrica"])
    data = [[val], [lbl], [leg]]
    t = Table(data, colWidths=[40*mm])
    t.setStyle(TableStyle([
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 1),
        ("BOTTOMPADDING", (0,0), (-1,-1), 1),
        ("LEFTPADDING", (0,0), (-1,-1), 3),
        ("RIGHTPADDING", (0,0), (-1,-1), 3),
        ("BACKGROUND", (0,0), (-1,-1), BRANCO),
        ("BOX", (0,0), (-1,-1), 0.5, BORDA),
    ]))
    return t


def _classificacao_badge(classificacao: str, mapa: dict, estilos) -> Paragraph:
    """Retorna Paragraph com estilo de badge colorido."""
    info = mapa.get(classificacao, {})
    estilo_nome = info.get("estilo", "BadgeAzul")
    return Paragraph(info.get("rotulo", classificacao), estilos[estilo_nome])


# ═══════════════════════════════════════════════════════════════════════════════
def gerar_pdf_relatorio(
    metricas_vfc: dict,
    pss10_score: int | None,
    psqi: dict | None,
    ipaq: dict | None,
    stai: dict | None,
) -> BytesIO:
    """
    Gera o PDF do relatório e retorna um BytesIO com o conteúdo.

    Parâmetros
    ----------
    metricas_vfc : dict — resultado de banco.obter_metricas() + interpretacao_si
    pss10_score  : int | None
    psqi         : dict | None — resultado de banco.obter_psqi()
    ipaq         : dict | None — resultado de banco.obter_ipaq()
    stai         : dict | None — resultado de banco.obter_stai()
    """
    buf = BytesIO()
    estilos = _criar_estilos()

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=15*mm, bottomMargin=15*mm,
        title="Relatório PICMED — Resultados",
        author="PICMED UNICEPLAC",
    )

    historia = []

    # ── Cabeçalho ─────────────────────────────────────────────────────────
    historia.append(Paragraph("PICMED UNICEPLAC", estilos["TituloRelatorio"]))
    historia.append(Paragraph(
        "Relatório de Resultados — Análise de Variabilidade da Frequência Cardíaca",
        estilos["Subtitulo"]
    ))
    historia.append(Paragraph(
        "<b>ATENÇÃO:</b> Este relatório não constitui diagnóstico médico. "
        "Os dados aqui apresentados destinam-se exclusivamente a fins "
        "acadêmicos e informativos. Qualquer interpretação clínica deve "
        "ser realizada por profissional de saúde habilitado.",
        estilos["Disclaimer"]
    ))
    historia.append(Spacer(1, 4*mm))
    historia.append(_linha())

    # ── Métricas VFC ───────────────────────────────────────────────────────
    historia.append(Paragraph("1. Métricas de Variabilidade da Frequência Cardíaca (VFC)",
                               estilos["SecaoTitulo"]))

    si = metricas_vfc.get("si_baevsky")
    interpretacao_si = metricas_vfc.get("interpretacao_si", {})
    classificacao_si = interpretacao_si.get("rotulo", "N/D")

    # Determina cor do badge SI
    si_cor_bg = "#f1f5f9"
    si_cor_txt = "#64748b"
    if si is not None:
        if si < 50:   si_cor_txt = "#3b82f6"
        elif si < 150: si_cor_txt = "#22c55e"
        elif si < 300: si_cor_txt = "#d97706"
        else:          si_cor_txt = "#dc2626"

    # Linha 1: SI Baevsky (destaque)
    historia.append(Paragraph(
        f"<b>Índice de Estresse de Baevsky (SI):</b> "
        f"<font color='{si_cor_txt}'><b>{si if si is not None else 'N/D'}</b></font> — "
        f"{interpretacao_si.get('descricao', '')}",
        estilos["TextoNormal"]
    ))
    historia.append(Spacer(1, 3*mm))

    # Grid de métricas 4x2
    metricas_list = [
        ("SI Baevsky",     f"{si:.1f}" if si is not None else "—",
         "Índice de estresse autonômico.<br/>"
         "<50: vagal | 50–150: normal | ≥150: simpático"),
        ("FC Média",        f"{metricas_vfc.get('fc_media', '—')} bpm",
         "Frequência cardíaca média<br/>durante a coleta (batimentos/min)."),
        ("SDNN",            f"{metricas_vfc.get('sdnn_ms', '—')} ms",
         "Desvio padrão de todos os<br/>intervalos R-R normais. Reflete<br/>a VFC total."),
        ("RMSSD",           f"{metricas_vfc.get('rmssd_ms', '—')} ms",
         "Raiz quadrada da média das<br/>diferenças sucessivas ao quadrado.<br/>Reflete a atividade parassimpática."),
        ("pNN50",           f"{metricas_vfc.get('pnn50', '—')} %",
         "Percentual de intervalos R-R<br/>adjacentes com diferença >50 ms.<br/>Indicador de tônus vagal."),
        ("Mo",              f"{metricas_vfc.get('mo_ms', '—')} ms",
         "Moda do histograma R-R (ms).<br/>Valor mais frequente dos<br/>intervalos entre batimentos."),
        ("AMo",             f"{metricas_vfc.get('amo_pct', '—')} %",
         "Amplitude da Moda: percentual<br/>de batimentos no bin da moda.<br/>Quanto maior, menor a VFC."),
        ("MxDMn",           f"{metricas_vfc.get('mxdmn_ms', '—')} ms",
         "Variação total: RR máximo<br/>menos RR mínimo. Reflete a<br/>amplitude da arritmia sinusal."),
    ]

    cards_row = []
    for label, valor, legenda in metricas_list:
        cards_row.append(_metrica_card(label, valor, legenda, estilos))

    # Organiza em grid 4 colunas x 2 linhas
    grid = []
    for i in range(0, len(cards_row), 4):
        grid.append(cards_row[i:i+4])

    for row_data in grid:
        t = Table([row_data], colWidths=[40*mm]*4)
        t.setStyle(TableStyle([
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("LEFTPADDING", (0,0), (-1,-1), 2),
            ("RIGHTPADDING", (0,0), (-1,-1), 2),
        ]))
        historia.append(t)
        historia.append(Spacer(1, 2*mm))

    historia.append(_linha())

    # ── PSS-10 ─────────────────────────────────────────────────────────────
    historia.append(Paragraph("2. Escala de Estresse Percebido (PSS-10)",
                               estilos["SecaoTitulo"]))
    if pss10_score is not None:
        pss_cor = VERDE if pss10_score <= 13 else (AMBER if pss10_score <= 26 else VERMELHO)
        historia.append(Paragraph(
            f"<b>Escore:</b> <font color='{pss_cor}'><b>{pss10_score}</b></font> / 40 pontos<br/>"
            f"0–13: Baixo estresse | 14–26: Estresse moderado | 27–40: Alto estresse<br/>"
            "Referência: Cohen, S., Kamarck, T., & Mermelstein, R. (1983).",
            estilos["TextoNormal"]
        ))
    else:
        historia.append(Paragraph("PSS-10 não preenchido para esta sessão.",
                                   estilos["TextoNormal"]))
    historia.append(Spacer(1, 2*mm))
    historia.append(_linha())

    # ── PSQI ───────────────────────────────────────────────────────────────
    historia.append(Paragraph("3. Qualidade do Sono (PSQI)",
                               estilos["SecaoTitulo"]))
    if psqi:
        eg = psqi.get("escore_global", "—")
        mapa_psqi = {
            "boa":        {"rotulo": "Boa",        "estilo": "BadgeVerde"},
            "baixa":      {"rotulo": "Baixa",      "estilo": "BadgeAmber"},
            "ruim":       {"rotulo": "Ruim",       "estilo": "BadgeVermelho"},
            "muito_ruim": {"rotulo": "Muito Ruim", "estilo": "BadgeVermelho"},
        }
        badge_psqi = _classificacao_badge(psqi.get("classificacao", ""), mapa_psqi, estilos)
        t = Table([
            [Paragraph(f"<b>Escore Global:</b> {eg} / 21", estilos["TextoNormal"]),
             badge_psqi],
            [Paragraph(f"C1 (Qualidade): {psqi.get('componente_1','—')} &nbsp; "
                       f"C2 (Latência): {psqi.get('componente_2','—')} &nbsp; "
                       f"C3 (Duração): {psqi.get('componente_3','—')} &nbsp; "
                       f"C4 (Eficiência): {psqi.get('componente_4','—')}", estilos["TextoNormal"]),
             Paragraph("")],
            [Paragraph(f"C5 (Distúrbios): {psqi.get('componente_5','—')} &nbsp; "
                       f"C6 (Remédio): {psqi.get('componente_6','—')} &nbsp; "
                       f"C7 (Disfunção diurna): {psqi.get('componente_7','—')}", estilos["TextoNormal"]),
             Paragraph("")],
        ], colWidths=[120*mm, 40*mm])
        t.setStyle(TableStyle([
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING", (0,0), (-1,-1), 2),
            ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ]))
        historia.append(t)
        historia.append(Paragraph(
            "≤5: Boa &nbsp;|&nbsp; 6–10: Baixa &nbsp;|&nbsp; 11–15: Ruim &nbsp;|&nbsp; 16–21: Muito Ruim<br/>"
            "Referência: Buysse DJ et al. (1989). Psychiatry Research, 28(2):193-213.",
            estilos["LegendaMetrica"]
        ))
    else:
        historia.append(Paragraph("PSQI não preenchido para esta sessão.",
                                   estilos["TextoNormal"]))
    historia.append(Spacer(1, 2*mm))
    historia.append(_linha())

    # ── IPAQ ───────────────────────────────────────────────────────────────
    historia.append(Paragraph("4. Atividade Física (IPAQ versão curta)",
                               estilos["SecaoTitulo"]))
    if ipaq:
        mapa_ipaq = {
            "sedentario":    {"rotulo": "Sedentário",    "estilo": "BadgeAzul"},
            "insuficiente":  {"rotulo": "Insuficiente",  "estilo": "BadgeAmber"},
            "ativo":         {"rotulo": "Ativo",         "estilo": "BadgeVerde"},
            "muito_ativo":   {"rotulo": "Muito Ativo",   "estilo": "BadgeVerde"},
        }
        badge_ipaq = _classificacao_badge(ipaq.get("classificacao", ""), mapa_ipaq, estilos)
        met_total = ipaq.get("met_total", "—")
        t = Table([
            [Paragraph(f"<b>MET-min/semana:</b> {met_total}", estilos["TextoNormal"]),
             badge_ipaq],
            [Paragraph(f"Vigorosa: {ipaq.get('met_vigorosa','—')} &nbsp; "
                       f"Moderada: {ipaq.get('met_moderada','—')} &nbsp; "
                       f"Caminhada: {ipaq.get('met_caminhada','—')} &nbsp; "
                       f"Sentado: {ipaq.get('q7_sentado_horas',0)*60 + ipaq.get('q7_sentado_min',0)} min/dia",
                       estilos["TextoNormal"]),
             Paragraph("")],
        ], colWidths=[120*mm, 40*mm])
        t.setStyle(TableStyle([
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING", (0,0), (-1,-1), 2),
            ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ]))
        historia.append(t)
    else:
        historia.append(Paragraph("IPAQ não preenchido para esta sessão.",
                                   estilos["TextoNormal"]))
    historia.append(Spacer(1, 2*mm))
    historia.append(_linha())

    # ── STAI ───────────────────────────────────────────────────────────────
    historia.append(Paragraph("5. Ansiedade Estado/Traço (STAI-S-6 e STAI-T-6)",
                               estilos["SecaoTitulo"]))
    if stai:
        mapa_stai = {
            "baixo": {"rotulo": "Baixo", "estilo": "BadgeVerde"},
            "medio": {"rotulo": "Médio", "estilo": "BadgeAmber"},
            "alto":  {"rotulo": "Alto",  "estilo": "BadgeVermelho"},
        }
        badge_s = _classificacao_badge(stai.get("classificacao_s", ""), mapa_stai, estilos)
        badge_t = _classificacao_badge(stai.get("classificacao_t", ""), mapa_stai, estilos)
        t = Table([
            [Paragraph(f"<b>Estado (S):</b> {stai.get('score_s','—')}/24", estilos["TextoNormal"]),
             badge_s,
             Paragraph(f"<b>Traço (T):</b> {stai.get('score_t','—')}/24", estilos["TextoNormal"]),
             badge_t],
        ], colWidths=[52*mm, 28*mm, 52*mm, 28*mm])
        t.setStyle(TableStyle([
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING", (0,0), (-1,-1), 2),
            ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ]))
        historia.append(t)
        historia.append(Paragraph(
            "≤9: Baixo &nbsp;|&nbsp; 10–15: Médio &nbsp;|&nbsp; ≥16: Alto<br/>"
            "Versão curta de 6 itens validada em português brasileiro.<br/>"
            "Referência: Fioravanti-Bastos ACM, Cheniaux E, Landeira-Fernandez J (2011). "
            "Psicologia: Reflexão e Crítica, 24(3):485-494.",
            estilos["LegendaMetrica"]
        ))
    else:
        historia.append(Paragraph("STAI não preenchido para esta sessão.",
                                   estilos["TextoNormal"]))
    historia.append(Spacer(1, 6*mm))
    historia.append(_linha())

    # ── Rodapé ─────────────────────────────────────────────────────────────
    historia.append(Spacer(1, 8*mm))
    historia.append(Paragraph(
        "<b>PICMED UNICEPLAC</b><br/>"
        "Sistema de Coleta ECG/VFC — Estresse Oculto<br/>"
        "Este relatório foi gerado automaticamente e é destinado exclusivamente "
        "a fins acadêmicos e informativos. Os dados não possuem identificação "
        "do participante e não substituem avaliação profissional.",
        estilos["Disclaimer"]
    ))

    doc.build(historia)
    buf.seek(0)
    return buf