#!/usr/bin/env python3
"""Demo local isolado: trecho aleatorio enviado ao NeuroKit2 com escala configuravel."""

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


def _pick_session(conn: sqlite3.Connection, session_id: int | None, min_points: int) -> tuple[int, int]:
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
        if row is None:
            raise ValueError(f"Sessao {session_id} nao existe.")
        if int(row["n"]) < min_points:
            raise ValueError(
                f"Sessao {session_id} tem {int(row['n'])} amostras validas; minimo necessario: {min_points}."
            )
        return int(row["id"]), int(row["n"])

    row = conn.execute(
        """
        SELECT s.id, COUNT(e.id) AS n
        FROM sessoes s
        INNER JOIN ecg_bruto e ON e.id_sessao = s.id AND e.leads_on = 1
        GROUP BY s.id
        HAVING n >= ?
        ORDER BY s.id DESC
        LIMIT 1
        """,
        (min_points,),
    ).fetchone()
    if row is None:
        raise ValueError("Nenhuma sessao com ECG suficiente foi encontrada.")
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


def _random_segment(signal: np.ndarray, points: int, seed: int | None) -> tuple[np.ndarray, int, int]:
    if signal.size < points:
        raise ValueError("Sinal menor que o trecho solicitado.")
    rng = np.random.default_rng(seed)
    start = int(rng.integers(0, signal.size - points + 1))
    end = start + points
    return signal[start:end], start, end


def _valid_indices(values: object, n: int) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return np.array([], dtype=int)
    arr = arr[np.isfinite(arr)]
    arr = arr[(arr >= 0) & (arr < n)]
    return arr.astype(int)


def _plot_classification(
    sent_segment: np.ndarray,
    cleaned: np.ndarray,
    marks: dict[str, np.ndarray],
    fs: int,
    output_path: Path,
    title_suffix: str,
    show: bool,
) -> None:
    t = np.arange(sent_segment.size) / fs
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 7), sharex=True)

    ax1.plot(t, sent_segment, color="#374151", linewidth=1.0, label="ECG enviado ao NeuroKit2")
    ax1.set_title(f"Trecho enviado ao NeuroKit2 ({title_suffix})")
    ax1.set_ylabel("Amplitude (mV)")
    ax1.grid(True, alpha=0.25, linewidth=0.5)
    ax1.legend(loc="best")

    ax2.plot(t, cleaned, color="#1f77b4", linewidth=1.0, label="ECG_Clean")
    styles = {
        "ECG_P_Peaks": ("P", "#16a34a"),
        "ECG_Q_Peaks": ("Q", "#f59e0b"),
        "ECG_R_Peaks": ("R", "#dc2626"),
        "ECG_S_Peaks": ("S", "#7c3aed"),
        "ECG_T_Peaks": ("T", "#0891b2"),
    }
    for key, (label, color) in styles.items():
        idx = marks.get(key, np.array([], dtype=int))
        if idx.size > 0:
            ax2.scatter(t[idx], cleaned[idx], s=24, color=color, label=f"{label} ({idx.size})", zorder=3)

    ax2.set_title("Classificacao do NeuroKit2 (picos P, Q, R, S, T)")
    ax2.set_xlabel("Tempo (s)")
    ax2.set_ylabel("Amplitude (mV)")
    ax2.grid(True, alpha=0.25, linewidth=0.5)
    ax2.legend(loc="best", ncols=3)

    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    if show:
        plt.show()
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sorteia um trecho ECG aleatorio da sessao e plota a classificacao do NeuroKit2."
    )
    parser.add_argument("--db", default="estudo_picmed.db", help="Caminho para o banco SQLite.")
    parser.add_argument("--session-id", type=int, default=None, help="ID da sessao (opcional).")
    parser.add_argument("--fs", type=int, default=250, help="Taxa de amostragem em Hz.")
    parser.add_argument("--duration", type=float, default=12.0, help="Duracao do trecho aleatorio (s).")
    parser.add_argument(
        "--scale",
        type=float,
        default=-1.0,
        help="Escala aplicada ao trecho antes do NeuroKit2 (padrao: -1.0).",
    )
    parser.add_argument("--seed", type=int, default=None, help="Semente para repetir o mesmo sorteio.")
    parser.add_argument(
        "--output",
        default=None,
        help="Arquivo PNG de saida. Padrao: /tmp/neurokit2_classificacao_sessao_<id>.png",
    )
    parser.add_argument("--show", action="store_true", help="Abre o grafico na tela.")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Erro: banco nao encontrado: {db_path}", file=sys.stderr)
        sys.exit(1)

    points = max(1, int(args.duration * args.fs))
    with _connect_read_only(db_path) as conn:
        session_id, total_points = _pick_session(conn, args.session_id, points)
        signal = _load_signal(conn, session_id)

    segment, start, end = _random_segment(signal, points, args.seed)
    segment_sent = args.scale * segment

    try:
        signals, info = nk.ecg_process(segment_sent, sampling_rate=args.fs)
    except Exception as e:
        print(f"Erro no NeuroKit2 (ecg_process): {e}", file=sys.stderr)
        sys.exit(1)

    cleaned = np.asarray(signals["ECG_Clean"].values, dtype=float)
    marks: dict[str, np.ndarray] = {}
    for key in ("ECG_P_Peaks", "ECG_Q_Peaks", "ECG_R_Peaks", "ECG_S_Peaks", "ECG_T_Peaks"):
        marks[key] = _valid_indices(info.get(key, []), cleaned.size)

    output = (
        Path(args.output)
        if args.output
        else Path(f"/tmp/neurokit2_classificacao_sessao_{session_id}.png")
    )
    _plot_classification(
        sent_segment=segment_sent,
        cleaned=cleaned,
        marks=marks,
        fs=args.fs,
        output_path=output,
        title_suffix=f"sessao={session_id}, amostras={start}:{end}, scale={args.scale}",
        show=args.show,
    )

    rr_s = np.diff(marks["ECG_R_Peaks"]) / args.fs if marks["ECG_R_Peaks"].size >= 2 else np.array([], dtype=float)
    rr_valid = rr_s[(rr_s >= 0.30) & (rr_s <= 2.00)]
    bpm = 60.0 / np.mean(rr_valid) if rr_valid.size > 0 else float("nan")

    print(f"Sessao usada: {session_id}")
    print(f"Amostras validas na sessao: {total_points}")
    print(f"Trecho aleatorio escolhido: [{start}:{end}] ({segment.size / args.fs:.2f} s)")
    print(f"Escala aplicada antes do NeuroKit2: {args.scale}")
    print(f"Seed usada: {args.seed if args.seed is not None else 'aleatoria'}")
    print("Classificacao NeuroKit2 no trecho:")
    for key in ("ECG_P_Peaks", "ECG_Q_Peaks", "ECG_R_Peaks", "ECG_S_Peaks", "ECG_T_Peaks"):
        print(f"  - {key}: {marks[key].size}")
    print(f"RR validos (300-2000 ms): {rr_valid.size}")
    if rr_valid.size > 0:
        print(f"RR medio: {np.mean(rr_valid) * 1000.0:.1f} ms")
        print(f"BPM estimado: {bpm:.1f}")
    print(f"Grafico salvo em: {output}")


if __name__ == "__main__":
    main()
