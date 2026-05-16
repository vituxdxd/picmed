"""
Gerador de PDF — Relatório de Resultados PICMED

Novo estilo baseado em Markdown + Pandoc + XeLaTeX.
"""

from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import tempfile

import matplotlib.pyplot as plt
import numpy as np


def _valor(valor, sufixo: str = "", casas: int | None = None) -> str:
    if valor is None:
        return "N/D"
    if isinstance(valor, (int, float)):
        if casas is None:
            numero = f"{valor}"
        else:
            numero = f"{valor:.{casas}f}"
        return f"{numero}{sufixo}"
    return f"{valor}{sufixo}"


def _classificacao_pss10(score: int | None) -> tuple[str, str]:
    if score is None:
        return "N/D", "Questionário não preenchido."
    if score <= 13:
        return "Baixo estresse", "Faixa 0-13."
    if score <= 26:
        return "Estresse moderado", "Faixa 14-26."
    return "Alto estresse", "Faixa 27-40."


def _montar_secao_ecg(
    amostras_ecg: list[dict] | None,
    fs: int = 250,
    duracao_trecho_s: int = 12,
) -> tuple[str, Path | None]:
    titulo = f"# 1. Trecho do ECG analisado ({duracao_trecho_s} s, repouso)"

    if not amostras_ecg:
        return (
            f"""{titulo}

Não foi possível gerar o gráfico do ECG porque não há amostras disponíveis para esta sessão.
""",
            None,
        )

    sinal = np.array(
        [a.get("tensao_mv") for a in amostras_ecg if a.get("tensao_mv") is not None],
        dtype=float,
    )
    sinal = sinal[np.isfinite(sinal)]

    if sinal.size < fs * 4:
        return (
            f"""{titulo}

Não foi possível gerar o gráfico do ECG porque o trecho válido é curto demais para visualização confiável.
""",
            None,
        )

    pontos_trecho = min(sinal.size, fs * duracao_trecho_s)
    idx_inicio = max(0, (sinal.size - pontos_trecho) // 2)
    idx_fim = idx_inicio + pontos_trecho
    trecho = sinal[idx_inicio:idx_fim]
    tempo = np.arange(trecho.size) / fs

    arquivo_png = tempfile.NamedTemporaryFile(
        suffix=".png",
        prefix="picmed_ecg_",
        delete=False,
    )
    arquivo_png.close()
    caminho_figura = Path(arquivo_png.name)

    fig, ax = plt.subplots(figsize=(9.5, 2.8))
    ax.plot(tempo, trecho, color="#1e3a8a", linewidth=1.0)
    ax.set_xlabel("Tempo (s)")
    ax.set_ylabel("Amplitude (mV)")
    ax.set_title("Trecho central do ECG do participante")
    ax.grid(True, alpha=0.25, linewidth=0.5)
    fig.tight_layout()
    fig.savefig(caminho_figura, dpi=180)
    plt.close(fig)

    inicio_s = idx_inicio / fs
    fim_s = idx_fim / fs

    secao = f"""{titulo}

O gráfico abaixo mostra um trecho central do sinal coletado nesta sessão, usado como referência visual para as métricas de VFC apresentadas na sequência.

![Trecho do ECG do participante]({caminho_figura.as_posix()})

**Legenda de leitura rápida**
- **Derivação do traçado:** Derivação II (DII).
- **Leitura de valores negativos:** para considerar apenas magnitude, basta multiplicar o valor negativo por -1.
- **Janela exibida:** {len(trecho) / fs:.1f} s (de {inicio_s:.1f} s até {fim_s:.1f} s da sessão).
- **Amostras válidas no gráfico:** {len(trecho)} pontos a {fs} Hz.
- **Amplitude observada:** de {float(np.min(trecho)):.2f} mV a {float(np.max(trecho)):.2f} mV.
"""
    return secao, caminho_figura


def _montar_markdown(
    metricas_vfc: dict,
    pss10_score: int | None,
    psqi: dict | None,
    ipaq: dict | None,
    stai: dict | None,
    secao_ecg_markdown: str,
) -> str:
    si = metricas_vfc.get("si_baevsky")
    interpretacao_si = metricas_vfc.get("interpretacao_si", {})
    rotulo_si = interpretacao_si.get("rotulo", "N/D")
    descricao_si = interpretacao_si.get(
        "descricao", "Sem interpretação disponível para esta sessão."
    )
    pss_rotulo, pss_faixa = _classificacao_pss10(pss10_score)
    pss_legenda = "0-13: baixo | 14-26: moderado | 27-40: alto"

    psqi_class = "N/D"
    if psqi:
        mapa_psqi = {
            "boa": "Boa",
            "baixa": "Baixa",
            "ruim": "Ruim",
            "muito_ruim": "Muito ruim",
        }
        psqi_class = mapa_psqi.get(psqi.get("classificacao"), "N/D")
    psqi_legenda = "0-5: boa | 6-10: baixa | 11-15: ruim | 16-21: muito ruim"

    ipaq_class = "N/D"
    if ipaq:
        mapa_ipaq = {
            "sedentario": "Sedentário",
            "insuficiente": "Insuficiente",
            "ativo": "Ativo",
            "muito_ativo": "Muito ativo",
        }
        ipaq_class = mapa_ipaq.get(ipaq.get("classificacao"), "N/D")
    ipaq_legenda = "0: sedentário | <600: insuficiente | 600-1499: ativo | >=1500: muito ativo"

    stai_s = "N/D"
    stai_t = "N/D"
    if stai:
        mapa_stai = {"baixo": "Baixo", "medio": "Médio", "alto": "Alto"}
        stai_s = mapa_stai.get(stai.get("classificacao_s"), "N/D")
        stai_t = mapa_stai.get(stai.get("classificacao_t"), "N/D")
    stai_legenda = "6-9: baixo | 10-15: médio | 16-24: alto"
    si_legenda = "Vagal (<50) | Equilibrado (50-149) | Atenção (150-299) | Alto risco (>=300)"

    met_sentado = "N/D"
    if ipaq:
        sentado_total_min = ipaq.get("sentado_total_min")
        if sentado_total_min is None:
            sentado_total_min = (ipaq.get("q7_sentado_horas", 0) * 60) + ipaq.get(
                "q7_sentado_min", 0
            )
        met_sentado = _valor(
            sentado_total_min,
            " min/dia",
        )

    return f"""---
title: "Identificando estresse oculto por meio da variabilidade da frequência cardíaca em acadêmicos de medicina"
subtitle: "Relatório individual de variabilidade da frequência cardíaca e questionários"
author: "PICMED UNICEPLAC"
date: "{date.today().isoformat()}"
lang: "pt-BR"
titlepage: true
titlepage-color: "1e3a8a"
titlepage-text-color: "ffffff"
titlepage-rule-color: "ffffff"
titlepage-rule-height: 2
book: true
toc: true
toc-depth: 2
---

> AVISO IMPORTANTE: Este relatório foi elaborado exclusivamente para fins acadêmicos e informativos. O presente documento não constitui um diagnóstico ou laudo técnico, tampouco substitui a consulta e a avaliação clínica de um médico, psicólogo ou qualquer outro profissional de saúde habilitado.

# Resumo executivo

- **SI Baevsky:** {_valor(si, casas=1)}
- **Classificação SI:** {rotulo_si} — *{si_legenda}*
- **PSS-10:** {_valor(pss10_score)} / 40 ({pss_rotulo}) — *Legenda: {pss_legenda}*
- **PSQI (sono):** {_valor(psqi.get("escore_global") if psqi else None)} / 21 ({psqi_class}) — *Legenda: {psqi_legenda}*
- **IPAQ:** {ipaq_class} — *Legenda (MET-min/semana): {ipaq_legenda}*
- **STAI Estado/Traço:** {stai_s} / {stai_t} — *Legenda: {stai_legenda}*

{secao_ecg_markdown}

# 2. Métricas de VFC

As métricas de VFC refletem como o sistema nervoso autônomo está modulando o coração naquele momento da coleta.

| Métrica | Seu resultado | O que representa | Como interpretar no relatório |
| --- | --- | --- | --- |
| **SI Baevsky** | {_valor(si, casas=1)} | Índice de carga autonômica/estresse fisiológico calculado a partir dos intervalos RR. | <50: Vagal; 50-149: Equilibrado; 150-299: Atenção; >=300: Alto risco. |
| **FC média** | {_valor(metricas_vfc.get("fc_media"), " bpm")} | Frequência cardíaca média durante o trecho analisado. | Útil como contexto fisiológico geral da coleta. |
| **SDNN** | {_valor(metricas_vfc.get("sdnn_ms"), " ms")} | Variabilidade global dos intervalos RR (desvio-padrão). | Maior SDNN tende a indicar maior adaptabilidade autonômica. |
| **RMSSD** | {_valor(metricas_vfc.get("rmssd_ms"), " ms")} | Oscilações batimento a batimento ligadas ao tônus parassimpático. | Maior RMSSD costuma indicar maior atividade vagal de curto prazo. |
| **NN50** | {_valor(metricas_vfc.get("nn50"))} | Número de pares de RR com diferença absoluta >50 ms. | Complementa RMSSD/pNN50 na leitura da variabilidade rápida. |
| **pNN50** | {_valor(metricas_vfc.get("pnn50"), " %")} | Percentual de pares RR com diferença >50 ms. | Percentuais maiores geralmente indicam maior modulação vagal. |
| **Mo (ms)** | {_valor(metricas_vfc.get("mo_ms"), " ms")} | Moda dos intervalos RR (intervalo mais frequente). | Base para cálculo do SI de Baevsky. |
| **AMo (%)** | {_valor(metricas_vfc.get("amo_pct"), " %")} | Percentual correspondente à moda dos RR. | AMo alta pode sugerir menor dispersão dos RR. |
| **MxDMn (ms)** | {_valor(metricas_vfc.get("mxdmn_ms"), " ms")} | Diferença entre maior e menor RR válido. | Valores maiores indicam maior amplitude global de VFC. |

**Leitura clínica automatizada do SI nesta sessão:** **{rotulo_si}** - {descricao_si}

> **Nota de interpretação:** valores de VFC sofrem influência de idade, sexo, condicionamento físico, sono, medicações, consumo de cafeína, momento do dia e qualidade do sinal de ECG.

# 3. Escala de Estresse Percebido (PSS-10)

O PSS-10 estima o **estresse percebido subjetivo** nas últimas semanas.  
Cada item varia de 0 a 4 e o escore total varia de 0 a 40.

| Indicador | Resultado |
| --- | --- |
| Escore | {_valor(pss10_score)} / 40 |
| Classificação | {pss_rotulo} |
| Faixa de referência | {pss_faixa} |

Interpretação usada no sistema: 0-13 (baixo), 14-26 (moderado), 27-40 (alto).

Referência: Cohen, S., Kamarck, T., & Mermelstein, R. (1983).

# 4. Qualidade do Sono (PSQI)

O PSQI avalia a qualidade do sono no último mês.  
Cada componente vai de 0 (melhor) a 3 (pior), com escore global de 0 a 21.

| Indicador | Resultado |
| --- | --- |
| Escore global | {_valor(psqi.get("escore_global") if psqi else None)} / 21 |
| Classificação | {psqi_class} |
| C1 (Qualidade) | {_valor(psqi.get("componente_1") if psqi else None)} |
| C2 (Latência) | {_valor(psqi.get("componente_2") if psqi else None)} |
| C3 (Duração) | {_valor(psqi.get("componente_3") if psqi else None)} |
| C4 (Eficiência) | {_valor(psqi.get("componente_4") if psqi else None)} |
| C5 (Distúrbios) | {_valor(psqi.get("componente_5") if psqi else None)} |
| C6 (Medicação) | {_valor(psqi.get("componente_6") if psqi else None)} |
| C7 (Disfunção diurna) | {_valor(psqi.get("componente_7") if psqi else None)} |

Classificação usada no sistema: 0-5 (boa), 6-10 (baixa), 11-15 (ruim), 16-21 (muito ruim).

Referência: Buysse DJ et al. (1989). *Psychiatry Research*, 28(2):193-213.

# 5. Atividade Física (IPAQ - versão curta)

O IPAQ (versão curta) estima gasto energético semanal por meio de MET-min/semana.

Fórmulas utilizadas:

- MET vigorosa = 8,0 x minutos/dia x dias/semana
- MET moderada = 4,0 x minutos/dia x dias/semana
- MET caminhada = 3,3 x minutos/dia x dias/semana
- MET total = soma das três categorias

| Indicador | Resultado |
| --- | --- |
| Classificação | {ipaq_class} |
| MET-min/semana | {_valor(ipaq.get("met_total") if ipaq else None)} |
| MET vigorosa | {_valor(ipaq.get("met_vigorosa") if ipaq else None)} |
| MET moderada | {_valor(ipaq.get("met_moderada") if ipaq else None)} |
| MET caminhada | {_valor(ipaq.get("met_caminhada") if ipaq else None)} |
| Dias ativos totais | {_valor(ipaq.get("dias_ativos_total") if ipaq else None, " dias/semana")} |
| Tempo sentado | {met_sentado} |

Classificação adotada no sistema: sedentário (0), insuficiente (<600), ativo (600-1499), muito ativo (>=1500 MET-min/semana).

Fonte do IPAQ validado para português: **(MATSUDO et al., 2001)**.

# 6. Ansiedade Estado/Traço (STAI-S-6 e STAI-T-6)

O STAI-S-6 (estado) reflete ansiedade **no momento atual** e o STAI-T-6 (traço) reflete ansiedade **habitual**.

| Indicador | Resultado |
| --- | --- |
| Estado (S) | {_valor(stai.get("score_s") if stai else None)} / 24 |
| Classificação S | {stai_s} |
| Traço (T) | {_valor(stai.get("score_t") if stai else None)} / 24 |
| Classificação T | {stai_t} |

Classificação usada no sistema: 6-9 (baixo), 10-15 (médio), 16-24 (alto).

Referência: Fioravanti-Bastos ACM, Cheniaux E, Landeira-Fernandez J (2011).

---

**PICMED UNICEPLAC**  
Sistema de Coleta ECG/VFC - Estresse Oculto  
Relatório gerado automaticamente para fins acadêmicos e informativos.  
**Este documento não substitui avaliação de profissional habilitado.**
"""


def _gerar_pdf_via_pandoc(markdown: str) -> bytes:
    pandoc_bin = shutil.which("pandoc")
    xelatex_bin = shutil.which("xelatex")

    if not pandoc_bin:
        raise RuntimeError(
            "Pandoc não encontrado no sistema. Instale com: sudo apt-get install pandoc"
        )
    if not xelatex_bin:
        raise RuntimeError(
            "XeLaTeX não encontrado no sistema. Instale com: sudo apt-get install texlive-xetex"
        )

    with tempfile.TemporaryDirectory(prefix="picmed_pdf_") as tmp_dir:
        arquivo_md = tempfile.NamedTemporaryFile(
            suffix=".md",
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=tmp_dir,
        )
        arquivo_pdf = tempfile.NamedTemporaryFile(
            suffix=".pdf",
            delete=False,
            dir=tmp_dir,
        )

        try:
            arquivo_md.write(markdown)
            arquivo_md.flush()
            arquivo_pdf.close()

            cmd = [
                pandoc_bin,
                arquivo_md.name,
                "-o",
                arquivo_pdf.name,
                "--pdf-engine=xelatex",
                "--toc",
                "--toc-depth=2",
                "-V",
                "geometry:margin=2.5cm",
                "-V",
                "fontsize=11pt",
                "-V",
                "linestretch=1.2",
                "-V",
                "documentclass:article",
                "-V",
                "colorlinks:true",
                "-V",
                "linkcolor:1e3a8a",
                "-V",
                "urlcolor:1e3a8a",
            ]

            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                erro = (proc.stderr or proc.stdout or "Erro desconhecido do Pandoc.").strip()
                raise RuntimeError(f"Falha ao compilar PDF com Pandoc/XeLaTeX: {erro}")

            with open(arquivo_pdf.name, "rb") as f:
                return f.read()
        finally:
            try:
                arquivo_md.close()
            except Exception:
                pass


def gerar_pdf_relatorio(
    metricas_vfc: dict,
    pss10_score: int | None,
    psqi: dict | None,
    ipaq: dict | None,
    stai: dict | None,
    amostras_ecg: list[dict] | None = None,
) -> BytesIO:
    """
    Gera o PDF do relatório e retorna um BytesIO com o conteúdo.
    """
    caminho_figura_ecg: Path | None = None
    try:
        secao_ecg_markdown, caminho_figura_ecg = _montar_secao_ecg(amostras_ecg)
        markdown = _montar_markdown(
            metricas_vfc=metricas_vfc,
            pss10_score=pss10_score,
            psqi=psqi,
            ipaq=ipaq,
            stai=stai,
            secao_ecg_markdown=secao_ecg_markdown,
        )
        pdf_bytes = _gerar_pdf_via_pandoc(markdown)
        buffer = BytesIO(pdf_bytes)
        buffer.seek(0)
        return buffer
    finally:
        if caminho_figura_ecg and caminho_figura_ecg.exists():
            caminho_figura_ecg.unlink()
