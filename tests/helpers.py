"""Builders for inspect-like logs and in-memory run rows used across the tests."""

from __future__ import annotations

import json
from pathlib import Path

from worklog.scanner import ScannedRun


def make_log(
    tmp_path,
    *,
    campaign="speechval-canary",
    model="canary-1b-v2",
    eval_model="melt/hf/canary",
    benchmark="fleurs24-st-xen-dev",
    slice_id="st-fleurs-de_en",
    task="st",
    status="success",
    metrics=None,
    post_scorers=(),
    dataset_id="fleurs-de_en",
    lang=None,
    src="de",
    tgt="en",
    samples=10,
    completed=10,
    created="2026-11-12T10:00:00+00:00",
    finished="2026-11-12T10:05:00+00:00",
) -> Path:
    """Write a minimal inspect-like log and return its absolute path."""
    if metrics is None:
        metrics = {"corpus_bleu": 24.5}
    ev = {
        "eval_id": slice_id,
        "created": created,
        "task": f"speech-{task}-{slice_id}",
        "task_args": {"dataset_id": dataset_id, "lang": lang},
        "dataset": {"name": benchmark, "location": "/nowhere", "samples": samples, "sample_ids": []},
        "model": eval_model,
        "metadata": {
            "melteval_model": model,
            "melteval_benchmark": benchmark,
            "melteval_slice": slice_id,
            "melteval_task": task,
            "melteval_campaign": campaign,
            "melteval_post_scorers": ",".join(post_scorers),
        },
    }
    body = {
        "version": 2,
        "status": status,
        "eval": ev,
        "results": {
            "total_samples": samples,
            "completed_samples": completed,
            "scores": [
                {
                    "name": "asr_scorer" if task.startswith("asr") else "st_scorer",
                    # inspect serialises each metric as an object, not a bare number.
                    "metrics": {name: {"name": name, "value": value} for name, value in metrics.items()},
                }
            ],
        },
    }
    if status == "success":
        body["stats"] = {"started_at": created, "completed_at": finished}
        body["samples"] = [
            {"id": f"s{i}", "metadata": {"task": task, "lang": tgt or lang, "src_lang": src, "tgt_lang": tgt}}
            for i in range(samples)
        ]
    else:
        body["samples"] = []

    folder = Path(tmp_path) / "logs" / model / benchmark / slice_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{created.replace(':', '-')}.json"
    path.write_text(json.dumps(body))
    return path.resolve()


def run(
    path="a.json",
    *,
    mtime_ns=1,
    size=1,
    campaign="c",
    model="m",
    benchmark="fleurs24-st-xen-dev",
    slice="st-fleurs-de_en",
    task="st",
    src="de",
    tgt="en",
    status="success",
    total_samples=10,
    completed_samples=10,
    created="2026-11-12T10:00:00+00:00",
    finished="2026-11-12T10:05:00+00:00",
    log_id="x",
    provider="",
    lang=None,
    metrics=None,
    pending=(),
) -> ScannedRun:
    """A ScannedRun with sensible defaults, for store/views tests."""
    return ScannedRun(
        path=path,
        mtime_ns=mtime_ns,
        size=size,
        campaign=campaign,
        model=model,
        benchmark=benchmark,
        slice=slice,
        task=task,
        src=src,
        tgt=tgt,
        status=status,
        total_samples=total_samples,
        completed_samples=completed_samples,
        created=created,
        finished=finished,
        log_id=log_id,
        provider=provider,
        lang=lang,
        metrics={"corpus_bleu": 20.0} if metrics is None else metrics,
        pending=pending,
    )
