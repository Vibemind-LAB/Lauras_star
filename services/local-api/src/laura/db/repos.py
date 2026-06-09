"""Small data-access helpers. Plain SQL, returning dicts (JSON-serialisable)."""

from __future__ import annotations

import json
from typing import Any

from ..util import new_id, utcnow_iso
from .database import Database

# SQLite rejects OFFSET without LIMIT; bigint-max is a portable "no limit" sentinel
# (valid on SQLite and PostgreSQL) so callers can offset without a page size.
_NO_LIMIT = 2**63 - 1


def _paginate(
    sql: str, params: list[Any], limit: int | None, offset: int
) -> tuple[str, list[Any]]:
    """Append portable ``LIMIT/OFFSET`` when paginating; pass-through otherwise."""
    if limit is None and not offset:
        return sql, params
    effective = _NO_LIMIT if limit is None else limit
    return sql + " LIMIT ? OFFSET ?", [*params, effective, offset]


def create_project(
    db: Database,
    *,
    name: str,
    rate_num: int,
    rate_den: int,
    drop_frame: bool,
    workspace_root: str,
    project_id: str | None = None,
    org_id: str | None = None,
) -> dict[str, Any]:
    pid = project_id or new_id()
    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO projects (id, name, sequence_rate_num, sequence_rate_den, "
            "drop_frame, workspace_root, created_at, org_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, name, rate_num, rate_den, int(drop_frame), workspace_root, now, org_id),
        )
    project = get_project(db, pid)
    assert project is not None
    return project


def get_project(db: Database, project_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row is not None else None


def list_projects(
    db: Database, *, org_id: str | None = None,
    limit: int | None = None, offset: int = 0,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM projects"
    params: list[Any] = []
    if org_id is not None:
        sql += " WHERE org_id = ?"
        params.append(org_id)
    sql += " ORDER BY created_at DESC"
    sql, params = _paginate(sql, params, limit, offset)
    with db.connection() as conn:
        rows = (conn.execute(sql, tuple(params)) if params else conn.execute(sql)).fetchall()
        return [dict(r) for r in rows]


def count_projects(db: Database, *, org_id: str | None = None) -> int:
    with db.connection() as conn:
        if org_id is not None:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM projects WHERE org_id = ?", (org_id,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()
        return int(row["n"])


def rename_project(db: Database, project_id: str, name: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("UPDATE projects SET name=? WHERE id=?", (name, project_id))
        return cur.rowcount > 0


def delete_project(db: Database, project_id: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        return cur.rowcount > 0


def get_job(db: Database, job_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row is not None else None


def get_fetch_job(db: Database, asset_id: str) -> dict[str, Any] | None:
    """The ingest.fetch job for an asset (keyed by its stable idempotency key)."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE idempotency_key = ?", (f"fetch:{asset_id}",)
        ).fetchone()
        return dict(row) if row is not None else None


def set_job_progress(db: Database, job_id: str, progress_json: str) -> None:
    """Store the latest progress sample for a job (throttled by the caller)."""
    with db.connection() as conn:
        conn.execute(
            "UPDATE jobs SET progress_json = ?, updated_at = ? WHERE id = ?",
            (progress_json, utcnow_iso(), job_id),
        )


def request_import_cancel(db: Database, asset_id: str) -> bool:
    """Flag the asset's ingest.fetch job for cooperative cancellation.

    The fetch handler polls :func:`is_import_cancelled` between progress ticks and
    aborts the in-flight download. Idempotent and a no-op when no fetch job exists
    (e.g. the import already finished). Returns True if a fetch job was flagged."""
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE idempotency_key = ?",
            (utcnow_iso(), f"fetch:{asset_id}"),
        )
        return cur.rowcount > 0


def is_import_cancelled(db: Database, asset_id: str) -> bool:
    """True if the asset's ingest.fetch job has a pending cancel request."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT cancel_requested FROM jobs WHERE idempotency_key = ?",
            (f"fetch:{asset_id}",),
        ).fetchone()
        return bool(row["cancel_requested"]) if row is not None else False


# --- assets ---------------------------------------------------------------
def create_asset(
    db: Database,
    *,
    project_id: str,
    type: str,
    display_name: str,
    source_path: str,
    asset_id: str | None = None,
    online: bool = True,
) -> dict[str, Any]:
    aid = asset_id or new_id()
    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO media_assets (id, project_id, type, display_name, source_path, "
            "online, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (aid, project_id, type, display_name, source_path, int(online), now),
        )
    asset = get_asset(db, aid)
    assert asset is not None
    return asset


def find_asset_by_source_path(
    db: Database, project_id: str, source_path: str
) -> dict[str, Any] | None:
    """First asset in a project with this source path (online preferred) — for relink."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM media_assets WHERE project_id=? AND source_path=? "
            "ORDER BY online DESC, created_at LIMIT 1",
            (project_id, source_path),
        ).fetchone()
        return dict(row) if row is not None else None


def get_asset(db: Database, asset_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM media_assets WHERE id = ?", (asset_id,)).fetchone()
        return dict(row) if row is not None else None


def list_assets(
    db: Database, project_id: str, *, limit: int | None = None, offset: int = 0
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM media_assets WHERE project_id = ? ORDER BY created_at DESC"
    sql, params = _paginate(sql, [project_id], limit, offset)
    with db.connection() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def count_assets(db: Database, project_id: str) -> int:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM media_assets WHERE project_id = ?", (project_id,)
        ).fetchone()
        return int(row["n"])


def update_asset_probe(
    db: Database,
    asset_id: str,
    *,
    type: str,
    duration_frames: int | None,
    rate_num: int | None,
    rate_den: int | None,
    audio_sample_rate: int | None,
    start_timecode: str | None,
    width: int | None,
    height: int | None,
    codec_video: str | None,
    codec_audio: str | None,
    is_vfr: bool,
    sha256: str | None,
) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE media_assets SET type=?, duration_frames=?, rate_num=?, rate_den=?, "
            "audio_sample_rate=?, start_timecode=?, width=?, height=?, codec_video=?, "
            "codec_audio=?, is_vfr=?, sha256=? WHERE id=?",
            (type, duration_frames, rate_num, rate_den, audio_sample_rate, start_timecode,
             width, height, codec_video, codec_audio, int(is_vfr), sha256, asset_id),
        )


def set_asset_source(
    db: Database, asset_id: str, *, source_path: str, online: bool
) -> None:
    """Point an asset at its (now local) source file and flip its online flag.

    Used by the URL-ingest fetch stage once the download is verified complete.
    """
    with db.transaction() as conn:
        conn.execute(
            "UPDATE media_assets SET source_path=?, online=? WHERE id=?",
            (source_path, int(online), asset_id),
        )


def add_asset_file(
    db: Database,
    *,
    asset_id: str,
    kind: str,
    path: str,
    size_bytes: int | None = None,
    checksum: str | None = None,
    is_proxy: bool = False,
    is_waveform: bool = False,
    is_audio_extract: bool = False,
) -> dict[str, Any]:
    """Idempotent per (asset_id, kind): replaces any existing file of that kind."""
    fid = new_id()
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM asset_files WHERE asset_id=? AND kind=?", (asset_id, kind)
        )
        conn.execute(
            "INSERT INTO asset_files (id, asset_id, kind, path, size_bytes, is_proxy, "
            "is_waveform, is_audio_extract, checksum) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fid, asset_id, kind, path, size_bytes, int(is_proxy), int(is_waveform),
             int(is_audio_extract), checksum),
        )
        row = conn.execute("SELECT * FROM asset_files WHERE id=?", (fid,)).fetchone()
    return dict(row)


def list_asset_files(db: Database, asset_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM asset_files WHERE asset_id=? ORDER BY kind", (asset_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# --- analysis -------------------------------------------------------------
def create_analysis_run(
    db: Database, *, asset_id: str, pipeline_version: str, config: dict[str, Any]
) -> dict[str, Any]:
    rid = new_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO analysis_runs (id, asset_id, pipeline_version, status, config_json) "
            "VALUES (?, ?, ?, 'queued', ?)",
            (rid, asset_id, pipeline_version, json.dumps(config)),
        )
    run = get_analysis_run(db, rid)
    assert run is not None
    return run


def get_analysis_run(db: Database, run_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row is not None else None


def get_latest_analysis_run(db: Database, asset_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM analysis_runs WHERE asset_id=? "
            "ORDER BY COALESCE(started_at, '') DESC, id DESC LIMIT 1",
            (asset_id,),
        ).fetchone()
        return dict(row) if row is not None else None


def start_analysis_run(db: Database, run_id: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE analysis_runs SET status='running', started_at=? WHERE id=?",
            (utcnow_iso(), run_id),
        )


def finish_analysis_run(
    db: Database, run_id: str, *, status: str, diagnostics: dict[str, Any]
) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE analysis_runs SET status=?, finished_at=?, diagnostics_json=? WHERE id=?",
            (status, utcnow_iso(), json.dumps(diagnostics), run_id),
        )


def clear_analysis_results(db: Database, *, asset_id: str, run_id: str) -> None:
    """Remove any prior shots/speakers/transcript for this run (idempotent re-run)."""
    with db.transaction() as conn:
        conn.execute("DELETE FROM shots WHERE analysis_run_id=?", (run_id,))
        conn.execute(
            "DELETE FROM transcript_words WHERE segment_id IN "
            "(SELECT id FROM transcript_segments WHERE analysis_run_id=?)",
            (run_id,),
        )
        conn.execute("DELETE FROM transcript_segments WHERE analysis_run_id=?", (run_id,))
        conn.execute("DELETE FROM speakers WHERE analysis_run_id=?", (run_id,))


def insert_shots(
    db: Database, *, asset_id: str, run_id: str, shots: list[dict[str, Any]]
) -> int:
    with db.transaction() as conn:
        for shot in shots:
            conn.execute(
                "INSERT INTO shots (id, asset_id, analysis_run_id, src_in_frame, "
                "src_out_frame_exclusive, confidence, method, thumbnail_path, "
                "black_ratio, static_score, phash, blur_score, keep, drop_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), asset_id, run_id, shot["src_in_frame"],
                 shot["src_out_frame_exclusive"], shot.get("confidence"),
                 shot.get("method"), shot.get("thumbnail_path"),
                 shot.get("black_ratio"), shot.get("static_score"), shot.get("phash"),
                 shot.get("blur_score"), 1 if shot.get("keep", True) else 0,
                 shot.get("drop_reason")),
            )
    return len(shots)


def list_shots(db: Database, asset_id: str, run_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM shots WHERE asset_id=? AND analysis_run_id=? ORDER BY src_in_frame",
            (asset_id, run_id),
        ).fetchall()
        return [dict(r) for r in rows]


def insert_speaker(
    db: Database, *, asset_id: str, run_id: str, label: str
) -> str:
    sid = new_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO speakers (id, asset_id, analysis_run_id, label) VALUES (?, ?, ?, ?)",
            (sid, asset_id, run_id, label),
        )
    return sid


def insert_segment_with_words(
    db: Database,
    *,
    asset_id: str,
    run_id: str,
    speaker_id: str | None,
    segment: dict[str, Any],
    words: list[dict[str, Any]],
) -> str:
    seg_id = new_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO transcript_segments (id, asset_id, analysis_run_id, speaker_id, "
            "start_sample, end_sample, start_frame, end_frame, text, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                seg_id, asset_id, run_id, speaker_id,
                segment["start_sample"], segment["end_sample"],
                segment["start_frame"], segment["end_frame"],
                segment["text"], segment.get("confidence"),
            ),
        )
        for word in words:
            conn.execute(
                "INSERT INTO transcript_words (id, segment_id, idx, start_sample, end_sample, "
                "start_frame, end_frame, text, confidence, is_punctuation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), seg_id, word["idx"], word["start_sample"], word["end_sample"],
                 word["start_frame"], word["end_frame"], word["text"], word.get("confidence"),
                 int(word.get("is_punctuation", False))),
            )
    return seg_id


def get_transcript(db: Database, asset_id: str, run_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        segs = conn.execute(
            "SELECT s.*, sp.label AS speaker_label FROM transcript_segments s "
            "LEFT JOIN speakers sp ON sp.id = s.speaker_id "
            "WHERE s.asset_id=? AND s.analysis_run_id=? ORDER BY s.start_sample",
            (asset_id, run_id),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for seg in segs:
            words = conn.execute(
                "SELECT * FROM transcript_words WHERE segment_id=? ORDER BY idx", (seg["id"],)
            ).fetchall()
            row = dict(seg)
            row["words"] = [dict(w) for w in words]
            out.append(row)
        return out


def list_words_for_run(db: Database, asset_id: str, run_id: str) -> list[dict[str, Any]]:
    """All transcript words for an asset+run, ordered by source ``start_frame``.

    Flat list across segments (the segment grouping is irrelevant for editorial cut
    alignment, which only needs the word timings). Each row also carries the diarization
    ``speaker_label`` of its segment (via the segment -> speaker join; ``NULL`` when the asset was
    not diarized) so semantic placement can detect speaker turns without a per-word schema column.
    Empty when the run has no transcript.
    """
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT w.*, sp.label AS speaker_label FROM transcript_words w "
            "JOIN transcript_segments s ON s.id = w.segment_id "
            "LEFT JOIN speakers sp ON sp.id = s.speaker_id "
            "WHERE s.asset_id=? AND s.analysis_run_id=? "
            "ORDER BY w.start_frame, w.end_frame",
            (asset_id, run_id),
        ).fetchall()
        return [dict(r) for r in rows]


def get_speaker(db: Database, speaker_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM speakers WHERE id=?", (speaker_id,)).fetchone()
        return dict(row) if row is not None else None


# --- timelines & exports --------------------------------------------------
def create_timeline(
    db: Database, *, project_id: str, name: str, kind: str, created_from: str | None = None
) -> dict[str, Any]:
    tid = new_id()
    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO timelines (id, project_id, name, kind, otio_json, created_from, "
            "created_at) VALUES (?, ?, ?, ?, '{}', ?, ?)",
            (tid, project_id, name, kind, created_from, now),
        )
    timeline = get_timeline(db, tid)
    assert timeline is not None
    return timeline


def get_timeline(db: Database, timeline_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM timelines WHERE id=?", (timeline_id,)).fetchone()
        return dict(row) if row is not None else None


def list_timelines(
    db: Database, project_id: str, *, limit: int | None = None, offset: int = 0
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM timelines WHERE project_id=? ORDER BY created_at DESC"
    sql, params = _paginate(sql, [project_id], limit, offset)
    with db.connection() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def count_timelines(db: Database, project_id: str) -> int:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM timelines WHERE project_id=?", (project_id,)
        ).fetchone()
        return int(row["n"])


def add_timeline_clip(
    db: Database,
    *,
    timeline_id: str,
    asset_id: str,
    src_in_frame: int,
    src_out_frame_exclusive: int,
    seq_in_frame: int,
    seq_out_frame_exclusive: int,
    lane: int = 0,
    speaker_id: str | None = None,
    origin_word_start_id: str | None = None,
    origin_word_end_id: str | None = None,
    speed_num: int = 1,
    speed_den: int = 1,
    audio_offset_samples: int = 0,
) -> dict[str, Any]:
    cid = new_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO timeline_clips (id, timeline_id, asset_id, src_in_frame, "
            "src_out_frame_exclusive, seq_in_frame, seq_out_frame_exclusive, lane, "
            "speaker_id, origin_word_start_id, origin_word_end_id, speed_num, speed_den, "
            "audio_offset_samples) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, timeline_id, asset_id, src_in_frame, src_out_frame_exclusive,
             seq_in_frame, seq_out_frame_exclusive, lane, speaker_id,
             origin_word_start_id, origin_word_end_id, speed_num, speed_den,
             audio_offset_samples),
        )
        row = conn.execute("SELECT * FROM timeline_clips WHERE id=?", (cid,)).fetchone()
    return dict(row)


def list_timeline_clips(db: Database, timeline_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM timeline_clips WHERE timeline_id=? ORDER BY seq_in_frame, lane",
            (timeline_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def create_interchange_export(
    db: Database,
    *,
    timeline_id: str,
    fmt: str,
    status: str,
    output_path: str | None,
    options: dict[str, Any],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    eid = new_id()
    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO interchange_exports"
            " (id, timeline_id, format, status, output_path, options_json,"
            " diagnostics_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, timeline_id, fmt, status, output_path, json.dumps(options),
             json.dumps(diagnostics), now),
        )
        row = conn.execute(
            "SELECT * FROM interchange_exports WHERE id=?", (eid,)
        ).fetchone()
    return dict(row)


def get_interchange_export(db: Database, export_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM interchange_exports WHERE id=?", (export_id,)
        ).fetchone()
        return dict(row) if row is not None else None


# --- render-pipeline exports -----------------------------------------------


def create_export(
    db: Database,
    *,
    project_id: str,
    timeline_id: str | None,
    format: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    eid = new_id()
    now = utcnow_iso()
    options_text = json.dumps(options) if options is not None else None
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO exports (id, project_id, timeline_id, format, status, options, created_at) "
            "VALUES (?, ?, ?, ?, 'rendering', ?, ?)",
            (eid, project_id, timeline_id, format, options_text, now),
        )
    row = get_export(db, eid)
    assert row is not None
    return row


def get_export(db: Database, export_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        r = conn.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
        if r is None:
            return None
        row = dict(r)
        raw = row.get("options")
        row["options"] = json.loads(raw) if raw else {}
        return row


def list_exports(db: Database, project_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM exports WHERE project_id=? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            row = dict(r)
            raw = row.get("options")
            row["options"] = json.loads(raw) if raw else {}
            out.append(row)
        return out


def set_export_done(db: Database, export_id: str, *, path: str, size_bytes: int) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE exports SET status='ready', path=?, size_bytes=? WHERE id=?",
                     (path, size_bytes, export_id))


def set_export_error(db: Database, export_id: str, error: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE exports SET status='error', error=? WHERE id=?", (error, export_id))


def get_word(db: Database, word_id: str) -> dict[str, Any] | None:
    """A transcript word joined with its segment's asset id."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT w.*, s.asset_id AS asset_id FROM transcript_words w "
            "JOIN transcript_segments s ON s.id = w.segment_id WHERE w.id = ?",
            (word_id,),
        ).fetchone()
        return dict(row) if row is not None else None


def replace_timeline_clips(
    db: Database, timeline_id: str, rows: list[dict[str, Any]]
) -> None:
    """Atomically replace all clips of a timeline (materialised edit result)."""
    with db.transaction() as conn:
        conn.execute("DELETE FROM timeline_clips WHERE timeline_id=?", (timeline_id,))
        for r in rows:
            conn.execute(
                "INSERT INTO timeline_clips (id, timeline_id, asset_id, src_in_frame, "
                "src_out_frame_exclusive, seq_in_frame, seq_out_frame_exclusive, lane, "
                "speaker_id, origin_word_start_id, origin_word_end_id, speed_num, speed_den, "
                "audio_offset_samples) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), timeline_id, r["asset_id"], r["src_in_frame"],
                 r["src_out_frame_exclusive"], r["seq_in_frame"], r["seq_out_frame_exclusive"],
                 r.get("lane", 0), r.get("speaker_id"), r.get("origin_word_start_id"),
                 r.get("origin_word_end_id"), r.get("speed_num", 1), r.get("speed_den", 1),
                 r.get("audio_offset_samples", 0)),
            )


def set_timeline_clip_audio_offsets(
    db: Database, timeline_id: str, offsets_by_src_in: dict[int, int]
) -> None:
    """Persist accepted L/J split offsets onto the clips (2-lane editable state, samples).

    ``offsets_by_src_in`` maps a clip's ``src_in_frame`` (the inter-clip cut that begins it, i.e.
    the planner's ``seq_cut``) to that clip's signed ``audio_offset_samples`` head offset
    (invariant #3). The whole timeline is rewritten wholesale: every clip not present in the map is
    reset to ``0`` (a hard cut), so re-posting a reduced accepted set takes the omitted splits back
    to hard cuts. The first clip has no leading cut and so always resolves to ``0``.
    """
    with db.transaction() as conn:
        conn.execute(
            "UPDATE timeline_clips SET audio_offset_samples=0 WHERE timeline_id=?",
            (timeline_id,),
        )
        for src_in_frame, samples in offsets_by_src_in.items():
            conn.execute(
                "UPDATE timeline_clips SET audio_offset_samples=? "
                "WHERE timeline_id=? AND src_in_frame=?",
                (int(samples), timeline_id, int(src_in_frame)),
            )


def update_timeline_otio(db: Database, timeline_id: str, otio_json: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE timelines SET otio_json=? WHERE id=?", (otio_json, timeline_id)
        )


# --- enterprise: orgs, users, memberships, api keys, audit -----------------
def create_org(db: Database, *, name: str) -> dict[str, Any]:
    oid = new_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO organizations (id, name, created_at) VALUES (?, ?, ?)",
            (oid, name, utcnow_iso()),
        )
        row = conn.execute("SELECT * FROM organizations WHERE id=?", (oid,)).fetchone()
    return dict(row)


def get_org(db: Database, org_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM organizations WHERE id=?", (org_id,)).fetchone()
        return dict(row) if row is not None else None


def create_user(db: Database, *, email: str, display_name: str | None = None) -> dict[str, Any]:
    uid = new_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users (id, email, display_name, created_at) VALUES (?, ?, ?, ?)",
            (uid, email, display_name, utcnow_iso()),
        )
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row)


def add_membership(db: Database, *, org_id: str, user_id: str, role: str) -> dict[str, Any]:
    mid = new_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO memberships (id, org_id, user_id, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, org_id, user_id, role, utcnow_iso()),
        )
        row = conn.execute("SELECT * FROM memberships WHERE id=?", (mid,)).fetchone()
    return dict(row)


def create_api_key(
    db: Database, *, org_id: str, user_id: str | None, name: str | None, prefix: str,
    key_hash: str, role: str,
) -> dict[str, Any]:
    kid = new_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO api_keys (id, org_id, user_id, name, prefix, key_hash, role, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kid, org_id, user_id, name, prefix, key_hash, role, utcnow_iso()),
        )
        row = conn.execute("SELECT * FROM api_keys WHERE id=?", (kid,)).fetchone()
    return dict(row)


def get_api_key_by_hash(db: Database, key_hash: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM api_keys WHERE key_hash=?", (key_hash,)).fetchone()
        return dict(row) if row is not None else None


def touch_api_key(db: Database, key_id: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE api_keys SET last_used_at=? WHERE id=?", (utcnow_iso(), key_id)
        )


def revoke_api_key(db: Database, key_id: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (key_id,))
        return cur.rowcount > 0


def insert_audit_event(
    db: Database, *, org_id: str | None, principal_kind: str, principal_id: str | None,
    action: str, entity_type: str | None, entity_id: str | None, payload: dict[str, Any],
) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO audit_events (id, org_id, principal_kind, principal_id, action, "
            "entity_type, entity_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id(), org_id, principal_kind, principal_id, action, entity_type, entity_id,
             json.dumps(payload), utcnow_iso()),
        )


def list_audit_events(db: Database, *, limit: int = 100) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_events ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# --- deletes / renames / transcript edits / search ------------------------
def delete_asset(db: Database, asset_id: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM media_assets WHERE id=?", (asset_id,))
        return cur.rowcount > 0


def rename_timeline(db: Database, timeline_id: str, name: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("UPDATE timelines SET name=? WHERE id=?", (name, timeline_id))
        return cur.rowcount > 0


def delete_timeline(db: Database, timeline_id: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM timelines WHERE id=?", (timeline_id,))
        return cur.rowcount > 0


def get_segment(db: Database, segment_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT s.*, sp.label AS speaker_label FROM transcript_segments s "
            "LEFT JOIN speakers sp ON sp.id = s.speaker_id WHERE s.id = ?",
            (segment_id,),
        ).fetchone()
        return dict(row) if row is not None else None


def update_segment(
    db: Database, segment_id: str, *, text: str | None = None, speaker_id: str | None = None
) -> bool:
    sets: list[str] = []
    params: list[Any] = []
    if text is not None:
        sets.append("text=?")
        params.append(text)
    if speaker_id is not None:
        sets.append("speaker_id=?")
        params.append(speaker_id)
    if not sets:
        return False
    params.append(segment_id)
    with db.transaction() as conn:
        cur = conn.execute(f"UPDATE transcript_segments SET {', '.join(sets)} WHERE id=?", params)
        return cur.rowcount > 0


def get_segment_words(db: Database, segment_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM transcript_words WHERE segment_id=? ORDER BY idx", (segment_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_words_in_range(
    db: Database, start_word_id: str, end_word_id: str
) -> list[dict[str, Any]]:
    """Words from ``start_word_id`` to ``end_word_id`` inclusive, ordered by idx.

    Returns ``[]`` when either word is missing or they are in different segments
    (cross-segment selections are not captioned as a single cue)."""
    w0 = get_word(db, start_word_id)
    w1 = get_word(db, end_word_id)
    if w0 is None or w1 is None or w0["segment_id"] != w1["segment_id"]:
        return []
    lo, hi = sorted((int(w0["idx"]), int(w1["idx"])))
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM transcript_words WHERE segment_id=? AND idx BETWEEN ? AND ? "
            "ORDER BY idx",
            (w0["segment_id"], lo, hi),
        ).fetchall()
        return [dict(r) for r in rows]


def search_transcript(
    db: Database, *, project_id: str, query: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Lexical, case-insensitive transcript search scoped to a project.

    Portable across SQLite/Postgres (LOWER + LIKE). FTS5/semantic search is a
    later optimisation (docs/15)."""
    pattern = f"%{query.lower()}%"
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT s.id AS segment_id, s.asset_id AS asset_id, a.display_name AS asset_name, "
            "s.start_frame AS start_frame, s.end_frame AS end_frame, s.text AS text, "
            "sp.label AS speaker_label "
            "FROM transcript_segments s "
            "JOIN media_assets a ON a.id = s.asset_id "
            "LEFT JOIN speakers sp ON sp.id = s.speaker_id "
            "WHERE a.project_id = ? AND LOWER(s.text) LIKE ? "
            "ORDER BY s.start_sample LIMIT ?",
            (project_id, pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_shot(db: Database, shot_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone()
        return dict(row) if row is not None else None


# --- scenes (rough-cut marker layer) ---------------------------------------

def get_scene(db: Database, scene_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM scenes WHERE id=?", (scene_id,)).fetchone()
        return dict(row) if row is not None else None


def list_scenes(db: Database, source_timeline_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM scenes WHERE source_timeline_id=? ORDER BY order_index",
            (source_timeline_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def replace_scenes(
    db: Database,
    project_id: str,
    source_timeline_id: str,
    ranges: list[tuple[int, int]],
) -> None:
    """Replace all scenes of a timeline with ``ranges`` (``(seq_in, seq_out_exclusive)``),
    ordered. Reassigns ids + ``order_index``; names are positional ("Szene N")."""
    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute("DELETE FROM scenes WHERE source_timeline_id=?", (source_timeline_id,))
        for i, (sin, sout) in enumerate(ranges):
            conn.execute(
                "INSERT INTO scenes (id, project_id, source_timeline_id, name, order_index, "
                "seq_in_frame, seq_out_frame_exclusive, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (new_id(), project_id, source_timeline_id, f"Szene {i + 1}", i, sin, sout, now),
            )


def update_scene_name(db: Database, scene_id: str, name: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE scenes SET name=? WHERE id=?", (name, scene_id))


def set_scene_timeline(db: Database, scene_id: str, timeline_id: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE scenes SET scene_timeline_id=? WHERE id=?", (timeline_id, scene_id))


def set_scene_music(db: Database, scene_id: str, asset_id: str, gain_percent: int) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE scenes SET music_asset_id=?, music_gain_percent=? WHERE id=?",
            (asset_id, gain_percent, scene_id),
        )


def clear_scene_music(db: Database, scene_id: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE scenes SET music_asset_id=NULL, music_gain_percent=100 WHERE id=?",
            (scene_id,),
        )


def get_scene_by_timeline(db: Database, scene_timeline_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM scenes WHERE scene_timeline_id=? LIMIT 1", (scene_timeline_id,)
        ).fetchone()
        return dict(row) if row is not None else None


# --- sequence items (stage 5) -----------------------------------------------

def get_or_create_project_sequence(db: Database, project_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM timelines WHERE project_id=? AND kind='sequence' "
            "ORDER BY created_at LIMIT 1",
            (project_id,),
        ).fetchone()
    if row is not None:
        return dict(row)
    return create_timeline(db, project_id=project_id, name="Sequenz", kind="sequence")


def list_sequence_items(db: Database, sequence_timeline_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM sequence_items WHERE sequence_timeline_id=? ORDER BY order_index",
            (sequence_timeline_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def replace_sequence_items(
    db: Database, sequence_timeline_id: str, scene_ids: list[str]
) -> None:
    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM sequence_items WHERE sequence_timeline_id=?", (sequence_timeline_id,)
        )
        for i, sid in enumerate(scene_ids):
            conn.execute(
                "INSERT INTO sequence_items (id, sequence_timeline_id, scene_id, order_index, "
                "created_at) VALUES (?,?,?,?,?)",
                (new_id(), sequence_timeline_id, sid, i, now),
            )


# --- rough-cut per asset + project-wide scene list -------------------------

def get_or_create_asset_rough_cut(
    db: Database, project_id: str, asset_id: str
) -> dict[str, Any]:
    """Return the newest rough_cut timeline for this asset, creating one if absent.

    Looks up ``timelines`` where ``project_id=?`` AND ``kind='rough_cut'`` AND
    ``created_from=asset_id``, ordered newest first.  If none exists, creates a
    fresh timeline via :func:`create_timeline` with ``created_from=asset_id``."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM timelines WHERE project_id=? AND kind='rough_cut' "
            "AND created_from=? ORDER BY created_at DESC LIMIT 1",
            (project_id, asset_id),
        ).fetchone()
    if row is not None:
        return dict(row)
    return create_timeline(
        db, project_id=project_id, name="Rough Cut", kind="rough_cut", created_from=asset_id
    )


def list_project_scenes(db: Database, project_id: str) -> list[dict[str, Any]]:
    """Scenes for the assemble bin — one set per source video, with thumbnail info.

    Returns scenes only from the *newest rough-cut timeline that still has scenes*
    for each source asset, so re-building a rough cut at a different bias no longer
    piles up stale duplicate scenes in the bin. Each scene is enriched with
    ``asset_id`` (the source video) and ``thumb_frame`` (a representative source
    frame) so the UI can render a thumbnail without extra round-trips.
    """
    with db.connection() as conn:
        timelines = conn.execute(
            "SELECT id, created_from FROM timelines "
            "WHERE project_id=? AND kind='rough_cut' ORDER BY created_at DESC, id DESC",
            (project_id,),
        ).fetchall()
        scene_rows = conn.execute(
            "SELECT * FROM scenes WHERE project_id=? ORDER BY source_timeline_id, order_index",
            (project_id,),
        ).fetchall()

    clip_cache: dict[str, list[dict[str, Any]]] = {}

    def clips_for(tid: str) -> list[dict[str, Any]]:
        if tid not in clip_cache:
            clip_cache[tid] = list_timeline_clips(db, tid)
        return clip_cache[tid]

    # Asset behind each rough-cut timeline: created_from, else its first clip's asset.
    timeline_asset: dict[str, str | None] = {}
    for t in timelines:
        aid = t["created_from"]
        if aid is None:
            cl = clips_for(t["id"])
            aid = cl[0]["asset_id"] if cl else None
        timeline_asset[t["id"]] = aid

    tids_with_scenes = {r["source_timeline_id"] for r in scene_rows}
    # Newest timeline (already ordered newest-first) per asset that actually has scenes.
    newest_for_asset: dict[str, str] = {}
    for t in timelines:
        aid = timeline_asset[t["id"]]
        if aid is not None and t["id"] in tids_with_scenes:
            newest_for_asset.setdefault(aid, t["id"])
    keep = set(newest_for_asset.values())
    # Never hide scenes whose source asset cannot be derived.
    keep |= {
        tid for tid in tids_with_scenes if timeline_asset.get(tid) is None
    }

    out: list[dict[str, Any]] = []
    for r in scene_rows:
        scene = dict(r)
        tid = scene["source_timeline_id"]
        if tid not in keep:
            continue
        clips = clips_for(tid)
        match = next(
            (
                c
                for c in clips
                if c["seq_in_frame"] <= scene["seq_in_frame"] < c["seq_out_frame_exclusive"]
            ),
            clips[0] if clips else None,
        )
        scene["asset_id"] = timeline_asset.get(tid) or (match["asset_id"] if match else None)
        scene["thumb_frame"] = int(match["src_in_frame"]) if match else 0
        out.append(scene)
    return out
