#!/usr/bin/env python3
"""Demo local e isolado para validar picos R e intervalos R-R com NeuroKit2."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

import matplotlib.pyplot as plt
import numpy as np

try:
    import neurokit2 as nk
except ImportError:
    print("Erro: neurokit2 nao instalado. Rode: pip install neurokit2", file=sys.stderr)
    sys.exit(1)


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(db_path.resolve()), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _pick_session(conn: sqlite3.Connection, session_id: int | None) -> tuple[int, int]:
    if session_id is not None:
        row = conn.execute(
            """
            SELECT s.id, COUNT(e.id) AS n
            FROM sessoes s
            LEFT JOIN ecg_bruto e ON e.id_sessao = s.id AND e.leads_on = 1
            WHERE s.id = ?
            GROUP BY s.id
            """,
            (session_id,),
        ).fetchone()
        if row is None or row["n"] == 0:
            raise ValueError(f"Sessao {session_id} nao existe ou nao possui ECG com leads_on=1.")
        return int(row["id"]), int(row["n"])

    row = conn.execute(
        """
        SELECT s.id, COUNT(e.id) AS n
        FROM sessoes s
        INNER JOIN ecg_bruto e ON e.id_sessao = s.id AND e.leads_on = 1
        GROUP BY s.id
        ORDER BY s.id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise ValueError("Nenhuma sessao com ECG (leads_on=1) foi encontrada.")
    return int(row["id"]), int(row["n"])


def _load_signal(conn: sqlite3.Connection, session_id: int) -> np.ndarray:
    rows = conn.execute(
        """
        SELECT tensao_mv
        FROM ecg_bruto
        WHERE id_sessao = ? AND leads_on = 1
        ORDER BY idx_amostra
        """,
        (session_id,),
    ).fetchall()
    signal = np.array([r["tensao_mv"] for r in rows], dtype=float)
    signal = signal[np.isfinite(signal)]
    if signal.size == 0:
        raise ValueError("Sinal vazio ou invalido.")
    return signal


def _slice_center(signal: np.ndarray, fs: int, duration_s: float) -> tuple[np.ndarray, int, int]:
    points = max(1, int(duration_s * fs))
    points = min(points, signal.size)
    start = max(0, (signal.size - points) // 2)
    end = start + points
    return signal[start:end], start, end


def _median_peak_prominence(cleaned: np.ndarray, peaks: np.ndarray, fs: int) -> float:
    if peaks.size == 0:
        return float("-inf")

    half_window = max(1, int(0.06 * fs))
    prominences = []
    for idx in peaks:
        start = max(0, idx - half_window)
        end = min(cleaned.size, idx + half_window + 1)
        baseline = np.median(cleaned[start:end])
        prominences.append(cleaned[idx] - baseline)
    return float(np.median(prominences))


def _detect_best_polarity(segment: np.ndarray, fs: int, polarity_mode: str) -> tuple[dict, list[dict]]:
    candidates: list[dict] = []
    for polarity, factor in (("normal", 1.0), ("invertida", -1.0)):
        used = factor * segment
        cleaned = nk.ecg_clean(used, sampling_rate=fs, method="neurokit")
        _, info = nk.ecg_peaks(cleaned, sampling_rate=fs, method="neurokit")

        peaks = np.asarray(info.get("ECG_R_Peaks", []), dtype=int)
        rr_s = np.diff(peaks) / fs if peaks.size >= 2 else np.array([], dtype=float)
        rr_valid = rr_s[(rr_s >= 0.30) & (rr_s <= 2.00)]
        median_peak_amp = float(np.median(cleaned[peaks])) if peaks.size > 0 else float("-inf")
        median_prom = _median_peak_prominence(cleaned, peaks, fs)

        candidates.append({
            "polarity": polarity,
            "signal_used": used,
            "cleaned": cleaned,
            "peaks": peaks,
            "rr_s": rr_s,
            "rr_valid": rr_valid,
            "median_peak_amp": median_peak_amp,
            "median_prominence": median_prom,
            "score": (rr_valid.size, median_prom, median_peak_amp),
        })

    if polarity_mode in {"normal", "invertida"}:
        best = next(c for c in candidates if c["polarity"] == polarity_mode)
    else:
        best = max(candidates, key=lambda c: c["score"])

    if best["peaks"].size < 2:
        raise RuntimeError("Falha ao detectar picos R suficientes no trecho selecionado.")
    return best, candidates


def _plot_with_r_peaks(
    original_signal: np.ndarray,
    signal_used: np.ndarray,
    peaks: np.ndarray,
    fs: int,
    output_path: Path,
    polarity: str,
    show: bool,
) -> None:
    t = np.arange(signal_used.size) / fs
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), sharex=True)

    ax1.plot(t, original_signal, linewidth=1.0, color="#374151", label="Sinal bruto (original)")
    ax1.scatter(t[peaks], original_signal[peaks], color="crimson", s=20, zorder=3, label="Indices R")
    ax1.set_title("ECG bruto com indices detectados")
    ax1.set_ylabel("Amplitude (mV)")
    ax1.grid(True, alpha=0.25, linewidth=0.5)
    ax1.legend(loc="best")

    ax2.plot(t, signal_used, linewidth=1.0, color="#1f77b4", label=f"Sinal usado na deteccao ({polarity})")
    ax2.scatter(t[peaks], signal_used[peaks], color="crimson", s=20, zorder=3, label="Picos R")
    ax2.set_title("Sinal apos ajuste de polaridade para o NeuroKit2")
    ax2.set_xlabel("Tempo (s)")
    ax2.set_ylabel("Amplitude (mV)")
    ax2.grid(True, alpha=0.25, linewidth=0.5)
    ax2.legend(loc="best")

    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    if show:
        plt.show()
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Demo local para testar deteccao de picos R e intervalos R-R com NeuroKit2."
    )
    parser.add_argument("--db", default="estudo_picmed.db", help="Caminho para o banco SQLite.")
    parser.add_argument("--session-id", type=int, default=None, help="ID da sessao (opcional).")
    parser.add_argument("--fs", type=int, default=250, help="Taxa de amostragem em Hz.")
    parser.add_argument("--duration", type=float, default=12.0, help="Duracao do trecho (segundos).")
    parser.add_argument(
        "--output",
        default=None,
        help="Arquivo PNG de saida. Padrao: /tmp/rpeaks_sessao_<id>.png",
    )
    parser.add_argument(
        "--polarity",
        choices=("auto", "normal", "invertida"),
        default="auto",
        help="Modo de polaridade para detectar picos R.",
    )
    parser.add_argument("--show", action="store_true", help="Abre o grafico na tela.")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Erro: banco nao encontrado: {db_path}", file=sys.stderr)
        sys.exit(1)

    with _connect_read_only(db_path) as conn:
        session_id, total_points = _pick_session(conn, args.session_id)
        full_signal = _load_signal(conn, session_id)

    segment, start, end = _slice_center(full_signal, args.fs, args.duration)
    detection, candidates = _detect_best_polarity(segment, args.fs, args.polarity)

    rr_ms = detection["rr_s"] * 1000.0
    rr_valid_ms = detection["rr_valid"] * 1000.0
    bpm = 60.0 / np.mean(detection["rr_valid"]) if detection["rr_valid"].size > 0 else float("nan")

    output = (
        Path(args.output)
        if args.output
        else Path(f"/tmp/rpeaks_sessao_{session_id}.png")
    )
    _plot_with_r_peaks(
        original_signal=segment,
        signal_used=detection["signal_used"],
        peaks=detection["peaks"],
        fs=args.fs,
        output_path=output,
        polarity=detection["polarity"],
        show=args.show,
    )

    print(f"Sessao usada: {session_id}")
    print(f"Amostras no banco (leads_on=1): {total_points}")
    print(f"Trecho analisado: {segment.size} amostras ({segment.size / args.fs:.2f} s)")
    print(f"Janela no sinal completo: amostras [{start}:{end}]")
    print("Resumo por polaridade:")
    for c in candidates:
        print(
            f"  - {c['polarity']}: rr_validos={c['rr_valid'].size}, "
            f"prom_mediana={c['median_prominence']:.2f}, amp_mediana={c['median_peak_amp']:.2f}"
        )
    print(f"Modo de polaridade: {args.polarity}")
    print(f"Polaridade escolhida para deteccao: {detection['polarity']}")
    print(f"Picos R detectados: {detection['peaks'].size}")
    print(f"Intervalos R-R totais: {rr_ms.size}")
    print(f"Intervalos R-R validos (300-2000 ms): {rr_valid_ms.size}")
    if rr_valid_ms.size > 0:
        print(f"RR medio: {np.mean(rr_valid_ms):.1f} ms")
        print(f"BPM estimado no trecho: {bpm:.1f}")
    print(f"Primeiros 10 RR (ms): {np.round(rr_ms[:10], 1).tolist()}")
    print(f"Grafico salvo em: {output}")


if __name__ == "__main__":
    main()
