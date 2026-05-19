# =============================================================================
# CAMADA 2 — ARMAZENAMENTO
# Gerencia o banco de dados SQLite. Cria as tabelas, persiste participantes,
# sessões, amostras ECG brutas e métricas calculadas.
# =============================================================================

import sqlite3
import os
import threading
from datetime import datetime
from typing import Optional

# ─── Configuração ─────────────────────────────────────────────────────────────
DB_PATH = "estudo_picmed.db"

# Lock de escrita: SQLite suporta múltiplas leituras simultâneas,
# mas apenas uma escrita por vez — o lock evita "database is locked".
_lock_escrita = threading.Lock()


# ─── Conexão (sempre criar nova por thread) ───────────────────────────────────
def _conectar() -> sqlite3.Connection:
    """
    Retorna uma conexão SQLite com configurações otimizadas.
    Cada thread deve criar sua própria conexão (check_same_thread=False
    é seguro aqui porque usamos _lock_escrita para serializar escritas).
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row          # resultados como dicionários
    conn.execute("PRAGMA journal_mode=WAL") # Write-Ahead Log: leituras não bloqueiam escritas
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─── Inicialização do banco ───────────────────────────────────────────────────
def inicializar():
    """
    Cria todas as tabelas se ainda não existirem.
    Deve ser chamado uma vez no startup da aplicação.
    """
    with _lock_escrita:
        conn = _conectar()
        conn.executescript("""
            -- Participantes do estudo
            CREATE TABLE IF NOT EXISTS participantes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo          TEXT    NOT NULL UNIQUE,   -- ex: "P001" — anonimato
                ciclo           TEXT    NOT NULL,          -- basico | clinico | internato
                idade           INTEGER,
                sexo            TEXT,                      -- M | F | outro
                criado_em       TEXT    DEFAULT (datetime('now','localtime'))
            );

            -- Sessões de coleta (uma por participante)
            CREATE TABLE IF NOT EXISTS sessoes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                id_participante INTEGER NOT NULL REFERENCES participantes(id),
                inicio_em       TEXT    DEFAULT (datetime('now','localtime')),
                fim_em          TEXT,
                total_amostras  INTEGER DEFAULT 0,
                qualidade       TEXT    DEFAULT 'pendente', -- pendente | boa | ruim
                processada      INTEGER DEFAULT 0           -- 0 = não | 1 = sim
            );

            -- Amostras ECG brutas (inserção em lote para performance)
            CREATE TABLE IF NOT EXISTS ecg_bruto (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                id_sessao       INTEGER NOT NULL REFERENCES sessoes(id),
                idx_amostra     INTEGER NOT NULL,
                timestamp_us    INTEGER NOT NULL,
                adc_raw         INTEGER NOT NULL,
                tensao_mv       REAL    NOT NULL,
                leads_on        INTEGER NOT NULL  -- 0 ou 1
            );
            -- Índice para consultas por sessão (extrair R-R de uma sessão específica)
            CREATE INDEX IF NOT EXISTS idx_ecg_sessao ON ecg_bruto(id_sessao, idx_amostra);

            -- Métricas de VFC calculadas (uma linha por sessão)
            CREATE TABLE IF NOT EXISTS metricas_vfc (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                id_sessao       INTEGER NOT NULL UNIQUE REFERENCES sessoes(id),
                sdnn_ms         REAL,   -- desvio padrão de todos os R-R (ms)
                rmssd_ms        REAL,   -- raiz quadrada da média dos quadrados das diferenças
                nn50            INTEGER,-- pares de R-R com diferença > 50ms
                pnn50           REAL,   -- % de nn50
                fc_media        REAL,   -- frequência cardíaca média (bpm)
                mo_ms           REAL,   -- Modo do histograma R-R (ms)
                amo_pct         REAL,   -- Amplitude do Modo (%)
                mxdmn_ms        REAL,   -- RR_max - RR_min (ms)
                si_baevsky      REAL,   -- Índice de Estresse de Baevsky
                n_rr_validos    INTEGER,-- intervalos R-R usados no cálculo
                calculado_em    TEXT    DEFAULT (datetime('now','localtime'))
            );

            -- Inventário de rotina e PSS-10 (preenchido pelo operador)
            CREATE TABLE IF NOT EXISTS inventario (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                id_sessao       INTEGER NOT NULL UNIQUE REFERENCES sessoes(id),
                pss10_score     INTEGER,  -- 0–40
                pss10_answers   TEXT,     -- JSON array com 10 respostas [0-4]
                cafeina_mg      INTEGER,  -- mg de cafeína nas últimas 4h
                tabagismo       INTEGER,  -- 0=não | 1=sim
                tabagismo_freq  TEXT,     -- frequência (diario | semanal | ocasional)
                etilismo_24h    INTEGER,  -- 0=não | 1=sim
                medicacao       TEXT,     -- nome ou "nenhuma"
                psicoterapia    INTEGER DEFAULT 0, -- 0=não | 1=sim
                psicoterapia_freq TEXT     -- frequência (semanal | quinzenal | mensal | ocasional)
            );

            -- STAI-S-6 e STAI-T-6 (Fioravanti-Bastos et al., 2011)
            CREATE TABLE IF NOT EXISTS stai (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                id_sessao       INTEGER NOT NULL UNIQUE REFERENCES sessoes(id),
                s1_calmo        INTEGER,  -- 1-4 (ausente, inverte)
                s3_tenso        INTEGER,  -- 1-4 (presente)
                s5_vontade      INTEGER,  -- 1-4 (ausente, inverte)
                s12_nervoso     INTEGER,  -- 1-4 (presente)
                s15_descontraido INTEGER, -- 1-4 (ausente, inverte)
                s17_preocupado  INTEGER,  -- 1-4 (presente)
                t7_calmo        INTEGER,  -- 1-4 (ausente, inverte)
                t9_preocupa     INTEGER,  -- 1-4 (presente)
                t13_seguro      INTEGER,  -- 1-4 (ausente, inverte)
                t20_tenso_prob  INTEGER,  -- 1-4 (presente)
                t21_nerv_inquieto INTEGER, -- 1-4 (presente)
                t25_decisoes    INTEGER,  -- 1-4 (ausente, inverte)
                score_s         INTEGER,  -- 6-24
                score_t         INTEGER,  -- 6-24
                classificacao_s TEXT,     -- baixo | medio | alto
                classificacao_t TEXT      -- baixo | medio | alto
            );

            -- IPAQ: Questionário Internacional de Atividade Física (versão curta)
            CREATE TABLE IF NOT EXISTS ipaq (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                id_sessao       INTEGER NOT NULL UNIQUE REFERENCES sessoes(id),
                q1_vigorosa_dias    INTEGER,  -- dias/semana
                q2_vigorosa_min     INTEGER,  -- minutos/dia
                q3_moderada_dias    INTEGER,
                q4_moderada_min     INTEGER,
                q5_caminhada_dias   INTEGER,
                q6_caminhada_min    INTEGER,
                q7_sentado_horas    INTEGER,  -- horas
                q7_sentado_min      INTEGER,  -- minutos
                met_vigorosa        REAL,     -- MET-min/semana
                met_moderada        REAL,
                met_caminhada       REAL,
                met_total           REAL,
                dias_ativos_total   INTEGER,
                classificacao       TEXT      -- sedentario | insuficiente | ativo | muito_ativo
            );

            -- PSQI: Índice de Qualidade do Sono de Pittsburgh
            CREATE TABLE IF NOT EXISTS psqi (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                id_sessao       INTEGER NOT NULL UNIQUE REFERENCES sessoes(id),
                q1_hora_deitar      TEXT,    -- "HH:MM"
                q2_min_adormecer     INTEGER, -- minutos
                q3_hora_levantar     TEXT,    -- "HH:MM"
                q4_horas_sono        REAL,    -- horas
                q5a                  INTEGER, -- 0-3
                q5b                  INTEGER, -- 0-3
                q5c                  INTEGER, -- 0-3
                q5d                  INTEGER, -- 0-3
                q5e                  INTEGER, -- 0-3
                q5f                  INTEGER, -- 0-3
                q5g                  INTEGER, -- 0-3
                q5h                  INTEGER, -- 0-3
                q5i                  INTEGER, -- 0-3
                q5j                  INTEGER, -- 0-3
                q6_qualidade         INTEGER, -- 0-3
                q7_medicamento       INTEGER, -- 0-3
                q8_ficar_acordado    INTEGER, -- 0-3
                q9_entusiasmo        INTEGER, -- 0-3
                q10_parceiro         INTEGER, -- 0-3 (não entra no escore)
                componente_1     INTEGER, -- qualidade subjetiva (0-3)
                componente_2     INTEGER, -- latência do sono (0-3)
                componente_3     INTEGER, -- duração do sono (0-3)
                componente_4     INTEGER, -- eficiência habitual (0-3)
                componente_5     INTEGER, -- distúrbios do sono (0-3)
                componente_6     INTEGER, -- uso de remédio (0-3)
                componente_7     INTEGER, -- disfunção diurna (0-3)
                escore_global    INTEGER, -- 0-21
                classificacao    TEXT     -- boa | baixa | ruim | muito_ruim
            );
        """)
        conn.commit()
        conn.close()

    # Repara sessões órfãs: sessões com dados ECG mas sem fim_em.
    # Ocorre quando o firmware encerrou a leitura mas o callback #FIM_SESSAO
    # não foi disparado (ex: sistema reiniciado antes do processamento).
    reparar_sessoes_orfas()


# ─── Participantes ────────────────────────────────────────────────────────────
def criar_participante(codigo: str, ciclo: str, idade: int, sexo: str) -> int:
    """Insere novo participante. Retorna o id gerado."""
    with _lock_escrita:
        conn = _conectar()
        cur = conn.execute(
            "INSERT INTO participantes (codigo, ciclo, idade, sexo) VALUES (?,?,?,?)",
            (codigo.upper(), ciclo, idade, sexo)
        )
        conn.commit()
        pid = cur.lastrowid
        conn.close()
        return pid


def listar_participantes() -> list[dict]:
    conn = _conectar()
    rows = conn.execute("""
        SELECT p.*, COUNT(s.id) AS total_sessoes
        FROM participantes p
        LEFT JOIN sessoes s ON s.id_participante = p.id
        GROUP BY p.id
        ORDER BY p.criado_em DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def excluir_participante_completo(id_participante: int) -> Optional[dict]:
    """
    Remove participante e todos os dados relacionados (sessões, ECG bruto,
    métricas e inventário). Retorna um resumo da remoção.
    """
    with _lock_escrita:
        conn = _conectar()
        participante = conn.execute(
            "SELECT id, codigo FROM participantes WHERE id = ?",
            (id_participante,),
        ).fetchone()
        if not participante:
            conn.close()
            return None

        total_sessoes = conn.execute(
            "SELECT COUNT(*) FROM sessoes WHERE id_participante = ?",
            (id_participante,),
        ).fetchone()[0]
        total_amostras = conn.execute("""
            SELECT COUNT(*)
            FROM ecg_bruto
            WHERE id_sessao IN (
                SELECT id FROM sessoes WHERE id_participante = ?
            )
        """, (id_participante,)).fetchone()[0]

        conn.execute("""
            DELETE FROM stai
            WHERE id_sessao IN (
                SELECT id FROM sessoes WHERE id_participante = ?
            )
        """, (id_participante,))
        conn.execute("""
            DELETE FROM inventario
            WHERE id_sessao IN (
                SELECT id FROM sessoes WHERE id_participante = ?
            )
        """, (id_participante,))
        conn.execute("""
            DELETE FROM ipaq
            WHERE id_sessao IN (
                SELECT id FROM sessoes WHERE id_participante = ?
            )
        """, (id_participante,))
        conn.execute("""
            DELETE FROM psqi
            WHERE id_sessao IN (
                SELECT id FROM sessoes WHERE id_participante = ?
            )
        """, (id_participante,))
        conn.execute("""
            DELETE FROM metricas_vfc
            WHERE id_sessao IN (
                SELECT id FROM sessoes WHERE id_participante = ?
            )
        """, (id_participante,))
        conn.execute("""
            DELETE FROM ecg_bruto
            WHERE id_sessao IN (
                SELECT id FROM sessoes WHERE id_participante = ?
            )
        """, (id_participante,))
        conn.execute("DELETE FROM sessoes WHERE id_participante = ?", (id_participante,))
        conn.execute("DELETE FROM participantes WHERE id = ?", (id_participante,))
        conn.commit()
        conn.close()

        return {
            "id_participante": int(participante["id"]),
            "codigo": participante["codigo"],
            "total_sessoes": int(total_sessoes),
            "total_amostras": int(total_amostras),
        }


def excluir_sessao_completa(id_sessao: int) -> Optional[dict]:
    """
    Remove uma sessão específica e todos os dados relacionados
    (ECG bruto, métricas e formulários).
    """
    with _lock_escrita:
        conn = _conectar()
        sessao = conn.execute("""
            SELECT s.id, s.id_participante, p.codigo
            FROM sessoes s
            JOIN participantes p ON p.id = s.id_participante
            WHERE s.id = ?
        """, (id_sessao,)).fetchone()
        if not sessao:
            conn.close()
            return None

        total_amostras = conn.execute(
            "SELECT COUNT(*) FROM ecg_bruto WHERE id_sessao = ?",
            (id_sessao,),
        ).fetchone()[0]

        conn.execute("DELETE FROM stai WHERE id_sessao = ?", (id_sessao,))
        conn.execute("DELETE FROM inventario WHERE id_sessao = ?", (id_sessao,))
        conn.execute("DELETE FROM ipaq WHERE id_sessao = ?", (id_sessao,))
        conn.execute("DELETE FROM psqi WHERE id_sessao = ?", (id_sessao,))
        conn.execute("DELETE FROM metricas_vfc WHERE id_sessao = ?", (id_sessao,))
        conn.execute("DELETE FROM ecg_bruto WHERE id_sessao = ?", (id_sessao,))
        conn.execute("DELETE FROM sessoes WHERE id = ?", (id_sessao,))
        conn.commit()
        conn.close()

        return {
            "id_sessao": int(sessao["id"]),
            "id_participante": int(sessao["id_participante"]),
            "codigo": sessao["codigo"],
            "total_amostras": int(total_amostras),
        }


# ─── Sessões ──────────────────────────────────────────────────────────────────
def abrir_sessao(id_participante: int) -> int:
    """Cria uma nova sessão e retorna seu id."""
    with _lock_escrita:
        conn = _conectar()
        cur = conn.execute(
            "INSERT INTO sessoes (id_participante) VALUES (?)",
            (id_participante,)
        )
        conn.commit()
        sid = cur.lastrowid
        conn.close()
        return sid


def fechar_sessao(id_sessao: int, total_amostras: int, qualidade: str = "boa"):
    """Registra o encerramento da sessão."""
    with _lock_escrita:
        conn = _conectar()
        conn.execute("""
            UPDATE sessoes
            SET fim_em = datetime('now','localtime'),
                total_amostras = ?,
                qualidade = ?
            WHERE id = ?
        """, (total_amostras, qualidade, id_sessao))
        conn.commit()
        conn.close()


def listar_sessoes(id_participante: Optional[int] = None) -> list[dict]:
    conn = _conectar()
    query = """
        SELECT s.*, p.codigo, p.ciclo,
               m.si_baevsky, m.rmssd_ms, m.sdnn_ms,
               i.pss10_score,
               q.escore_global AS psqi_escore
        FROM sessoes s
        JOIN participantes p ON p.id = s.id_participante
        LEFT JOIN metricas_vfc m ON m.id_sessao = s.id
        LEFT JOIN inventario i ON i.id_sessao = s.id
        LEFT JOIN psqi q ON q.id_sessao = s.id
    """
    params: tuple = ()
    if id_participante is not None:
        query += " WHERE s.id_participante = ?"
        params = (id_participante,)
    query += " ORDER BY s.inicio_em DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── ECG Bruto ────────────────────────────────────────────────────────────────
# Buffer em memória antes de inserir em lote (muito mais rápido que INSERT um a um)
_buffer_ecg: list[tuple] = []
_buffer_id_sessao: Optional[int] = None
_TAMANHO_LOTE = 250  # flush a cada segundo de dados (250 Hz × 1s)


def bufferizar_amostra(id_sessao: int, idx: int, ts_us: int,
                        adc_raw: int, tensao_mv: float, leads_on: bool):
    """
    Adiciona amostra ao buffer em memória.
    Quando o buffer atinge 250 registros, faz um INSERT em lote.
    """
    global _buffer_ecg, _buffer_id_sessao

    _buffer_id_sessao = id_sessao
    _buffer_ecg.append((id_sessao, idx, ts_us, adc_raw, tensao_mv, 1 if leads_on else 0))

    if len(_buffer_ecg) >= _TAMANHO_LOTE:
        _flush_buffer()


def _flush_buffer():
    """Persiste o buffer de ECG no banco de dados."""
    global _buffer_ecg
    if not _buffer_ecg:
        return
    lote = _buffer_ecg[:]
    _buffer_ecg = []
    with _lock_escrita:
        conn = _conectar()
        conn.executemany(
            "INSERT INTO ecg_bruto (id_sessao,idx_amostra,timestamp_us,adc_raw,tensao_mv,leads_on) "
            "VALUES (?,?,?,?,?,?)",
            lote
        )
        conn.commit()
        conn.close()


def flush_final():
    """Persiste quaisquer amostras restantes no buffer ao encerrar a sessão."""
    _flush_buffer()


def carregar_ecg_sessao(id_sessao: int) -> list[dict]:
    """Retorna todas as amostras de uma sessão (somente leads_on=1)."""
    conn = _conectar()
    rows = conn.execute("""
        SELECT idx_amostra, timestamp_us, adc_raw, tensao_mv
        FROM ecg_bruto
        WHERE id_sessao = ? AND leads_on = 1
        ORDER BY idx_amostra
    """, (id_sessao,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Métricas VFC ─────────────────────────────────────────────────────────────
def salvar_metricas(id_sessao: int, metricas: dict):
    """Persiste o resultado do processamento VFC de uma sessão."""
    with _lock_escrita:
        conn = _conectar()
        conn.execute("""
            INSERT OR REPLACE INTO metricas_vfc
                (id_sessao, sdnn_ms, rmssd_ms, nn50, pnn50, fc_media,
                 mo_ms, amo_pct, mxdmn_ms, si_baevsky, n_rr_validos)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            id_sessao,
            metricas.get("sdnn_ms"), metricas.get("rmssd_ms"),
            metricas.get("nn50"),    metricas.get("pnn50"),
            metricas.get("fc_media"),metricas.get("mo_ms"),
            metricas.get("amo_pct"), metricas.get("mxdmn_ms"),
            metricas.get("si_baevsky"), metricas.get("n_rr_validos"),
        ))
        conn.execute("UPDATE sessoes SET processada=1 WHERE id=?", (id_sessao,))
        conn.commit()
        conn.close()


def salvar_inventario(id_sessao: int, dados: dict):
    """
    Persiste o inventário de rotina e PSS-10 de uma sessão.
    Faz merge com dados existentes para não perder campos salvos separadamente
    (ex: PSS-10 salvo via /api/pss10 e inventário via /api/inventario).
    """
    with _lock_escrita:
        conn = _conectar()
        # Busca a linha existente, se houver
        existente = conn.execute(
            "SELECT * FROM inventario WHERE id_sessao=?", (id_sessao,)
        ).fetchone()
        if existente:
            existente = dict(existente)
            # Mantém os valores existentes para campos não fornecidos nesta chamada
            merged = {
                "pss10_score":       dados.get("pss10_score", existente.get("pss10_score")),
                "pss10_answers":     dados.get("pss10_answers", existente.get("pss10_answers")),
                "cafeina_mg":        dados.get("cafeina_mg", existente.get("cafeina_mg")),
                "tabagismo":         dados.get("tabagismo", existente.get("tabagismo")),
                "tabagismo_freq":    dados.get("tabagismo_freq", existente.get("tabagismo_freq")),
                "etilismo_24h":      dados.get("etilismo_24h", existente.get("etilismo_24h")),
                "medicacao":         dados.get("medicacao", existente.get("medicacao")),
                "psicoterapia":      dados.get("psicoterapia", existente.get("psicoterapia")),
                "psicoterapia_freq": dados.get("psicoterapia_freq", existente.get("psicoterapia_freq")),
            }
            del existente  # libera o dict row do sqlite3
        else:
            merged = {
                "pss10_score":       dados.get("pss10_score"),
                "pss10_answers":     dados.get("pss10_answers"),
                "cafeina_mg":        dados.get("cafeina_mg"),
                "tabagismo":         dados.get("tabagismo"),
                "tabagismo_freq":    dados.get("tabagismo_freq"),
                "etilismo_24h":      dados.get("etilismo_24h"),
                "medicacao":         dados.get("medicacao"),
                "psicoterapia":      dados.get("psicoterapia"),
                "psicoterapia_freq": dados.get("psicoterapia_freq"),
            }

        conn.execute("""
            INSERT OR REPLACE INTO inventario
                (id_sessao, pss10_score, pss10_answers, cafeina_mg,
                 tabagismo, tabagismo_freq,
                 etilismo_24h, medicacao,
                 psicoterapia, psicoterapia_freq)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            id_sessao,
            merged["pss10_score"],
            merged.get("pss10_answers"),
            merged["cafeina_mg"],
            merged["tabagismo"],     merged["tabagismo_freq"],
            merged["etilismo_24h"],  merged["medicacao"],
            merged["psicoterapia"],  merged["psicoterapia_freq"],
        ))
        conn.commit()
        conn.close()


def salvar_psqi(id_sessao: int, respostas: dict):
    """Persiste as respostas brutas e os escores calculados do PSQI."""
    with _lock_escrita:
        conn = _conectar()
        conn.execute("""
            INSERT OR REPLACE INTO psqi
                (id_sessao, q1_hora_deitar, q2_min_adormecer, q3_hora_levantar,
                 q4_horas_sono, q5a, q5b, q5c, q5d, q5e, q5f, q5g, q5h, q5i, q5j,
                 q6_qualidade, q7_medicamento, q8_ficar_acordado, q9_entusiasmo,
                 q10_parceiro,
                 componente_1, componente_2, componente_3, componente_4,
                 componente_5, componente_6, componente_7,
                 escore_global, classificacao)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            id_sessao,
            respostas.get("q1_hora_deitar"),
            respostas.get("q2_min_adormecer"),
            respostas.get("q3_hora_levantar"),
            respostas.get("q4_horas_sono"),
            respostas.get("q5a"), respostas.get("q5b"), respostas.get("q5c"),
            respostas.get("q5d"), respostas.get("q5e"), respostas.get("q5f"),
            respostas.get("q5g"), respostas.get("q5h"), respostas.get("q5i"),
            respostas.get("q5j"),
            respostas.get("q6_qualidade"),
            respostas.get("q7_medicamento"),
            respostas.get("q8_ficar_acordado"),
            respostas.get("q9_entusiasmo"),
            respostas.get("q10_parceiro"),
            respostas.get("componente_1"), respostas.get("componente_2"),
            respostas.get("componente_3"), respostas.get("componente_4"),
            respostas.get("componente_5"), respostas.get("componente_6"),
            respostas.get("componente_7"),
            respostas.get("escore_global"),
            respostas.get("classificacao"),
        ))
        conn.commit()
        conn.close()


def salvar_stai(id_sessao: int, dados: dict):
    """Persiste os dados brutos e escores calculados do STAI-S-6 e STAI-T-6."""
    with _lock_escrita:
        conn = _conectar()
        conn.execute("""
            INSERT OR REPLACE INTO stai
                (id_sessao, s1_calmo, s3_tenso, s5_vontade, s12_nervoso,
                 s15_descontraido, s17_preocupado,
                 t7_calmo, t9_preocupa, t13_seguro, t20_tenso_prob,
                 t21_nerv_inquieto, t25_decisoes,
                 score_s, score_t, classificacao_s, classificacao_t)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            id_sessao,
            dados.get("s1_calmo"), dados.get("s3_tenso"), dados.get("s5_vontade"),
            dados.get("s12_nervoso"), dados.get("s15_descontraido"), dados.get("s17_preocupado"),
            dados.get("t7_calmo"), dados.get("t9_preocupa"), dados.get("t13_seguro"),
            dados.get("t20_tenso_prob"), dados.get("t21_nerv_inquieto"), dados.get("t25_decisoes"),
            dados.get("score_s"), dados.get("score_t"),
            dados.get("classificacao_s"), dados.get("classificacao_t"),
        ))
        conn.commit()
        conn.close()


def obter_stai(id_sessao: int) -> Optional[dict]:
    conn = _conectar()
    row = conn.execute(
        "SELECT * FROM stai WHERE id_sessao=?", (id_sessao,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def salvar_ipaq(id_sessao: int, dados: dict):
    """Persiste os dados brutos e calculados do IPAQ."""
    with _lock_escrita:
        conn = _conectar()
        conn.execute("""
            INSERT OR REPLACE INTO ipaq
                (id_sessao, q1_vigorosa_dias, q2_vigorosa_min,
                 q3_moderada_dias, q4_moderada_min,
                 q5_caminhada_dias, q6_caminhada_min,
                 q7_sentado_horas, q7_sentado_min,
                 met_vigorosa, met_moderada, met_caminhada, met_total,
                 dias_ativos_total, classificacao)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            id_sessao,
            dados.get("q1_vigorosa_dias"),
            dados.get("q2_vigorosa_min"),
            dados.get("q3_moderada_dias"),
            dados.get("q4_moderada_min"),
            dados.get("q5_caminhada_dias"),
            dados.get("q6_caminhada_min"),
            dados.get("q7_sentado_horas"),
            dados.get("q7_sentado_min"),
            dados.get("met_vigorosa"),
            dados.get("met_moderada"),
            dados.get("met_caminhada"),
            dados.get("met_total"),
            dados.get("dias_ativos_total"),
            dados.get("classificacao"),
        ))
        conn.commit()
        conn.close()


def obter_ipaq(id_sessao: int) -> Optional[dict]:
    conn = _conectar()
    row = conn.execute(
        "SELECT * FROM ipaq WHERE id_sessao=?", (id_sessao,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def obter_psqi(id_sessao: int) -> Optional[dict]:
    conn = _conectar()
    row = conn.execute(
        "SELECT * FROM psqi WHERE id_sessao=?", (id_sessao,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def obter_metricas(id_sessao: int) -> Optional[dict]:
    conn = _conectar()
    row = conn.execute(
        "SELECT * FROM metricas_vfc WHERE id_sessao=?", (id_sessao,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ─── Reparação de sessões órfãs ───────────────────────────────────────────────
def reparar_sessoes_orfas():
    """
    Varre sessões com fim_em=NULL que já possuem dados ECG salvos
    e as fecha automaticamente. Isso recupera sessões que o firmware
    encerrou mas cujo marcador #FIM_SESSAO não foi processado.
    """
    with _lock_escrita:
        conn = _conectar()
        orfas = conn.execute("""
            SELECT s.id, COUNT(e.id) as n_amostras
            FROM sessoes s
            INNER JOIN ecg_bruto e ON e.id_sessao = s.id
            WHERE s.fim_em IS NULL
              AND s.processada = 0
            GROUP BY s.id
            HAVING n_amostras > 0
        """).fetchall()

        reparadas = 0
        for row in orfas:
            conn.execute("""
                UPDATE sessoes
                SET fim_em = datetime('now','localtime'),
                    total_amostras = ?,
                    qualidade = 'boa'
                WHERE id = ?
            """, (row["n_amostras"], row["id"]))
            reparadas += 1

        conn.commit()
        conn.close()

    if reparadas > 0:
        print(f"[BANCO] Reparadas {reparadas} sessão(ões) órfã(s) com dados ECG.")


# ─── Estatísticas gerais ──────────────────────────────────────────────────────
def estatisticas_banco() -> dict:
    conn = _conectar()
    stats = {
        "total_participantes": conn.execute("SELECT COUNT(*) FROM participantes").fetchone()[0],
        "total_sessoes":        conn.execute("SELECT COUNT(*) FROM sessoes").fetchone()[0],
        "sessoes_processadas":  conn.execute("SELECT COUNT(*) FROM sessoes WHERE processada=1").fetchone()[0],
        "total_amostras_ecg":   conn.execute("SELECT COUNT(*) FROM ecg_bruto").fetchone()[0],
        "tamanho_db_mb": round(os.path.getsize(DB_PATH) / 1024 / 1024, 2) if os.path.exists(DB_PATH) else 0,
    }
    conn.close()
    return stats