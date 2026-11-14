import os

import pytest

from worklog.samples import (
    SCHEMA_VERSION,
    DigestError,
    context_file,
    load_digest,
    overview_of,
    per_sample_wer,
    prune,
    sample_record,
    select,
    sorts_for,
)

from .helpers import make_sample_log


def sample(**over):
    body = {
        "id": "s1",
        "epoch": 1,
        "input": "[asr 6.6s]",
        "target": "The reference sentence.",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Transcribe this English audio: "}]}],
        "output": {
            "model": "smurf//ckpt",
            "choices": [{"message": {"role": "assistant", "content": "the generated sentence"},
                         "stop_reason": "stop"}],
            "completion": "the generated sentence",
            "time": 2.5,
        },
        "scores": {
            "asr_scorer": {
                "value": {"wer_errors": 2, "ref_words": 10, "preamble": 0},
                "answer": "the generated sentence",
                "metadata": {},
                "history": [],
            }
        },
        "metadata": {
            "task": "asr", "lang": "en", "src_lang": "en", "tgt_lang": "",
            "cut_id": "cut-1", "duration": 6.6,
            "audio": {"kind": "shar", "dir": "/corpus/clean", "index": 3},
        },
        "store": {
            "melteval:prompt": "Transcribe this English audio: ",
            "melteval:format_spec": {"provider": "smurf", "apply_chat_template": False},
        },
        "total_time": 2.5,
        "working_time": 2.4,
        "started_at": "2026-11-12T10:00:00+00:00",
        "completed_at": "2026-11-12T10:00:03+00:00",
        "turn_count": 1,
        "error_retries": [],
    }
    body.update(over)
    return body


def test_record_keeps_what_the_page_renders():
    rec = sample_record(sample())
    assert rec["id"] == "s1"
    assert rec["completion"] == "the generated sentence"
    assert rec["stop_reason"] == "stop"
    assert rec["prompt"] == "Transcribe this English audio: "
    assert rec["format_spec"]["provider"] == "smurf"
    assert rec["metadata"]["audio"]["index"] == 3
    assert rec["scores"]["asr_scorer"]["answer"] == "the generated sentence"
    assert rec["scores"]["asr_scorer"]["history"] == 0
    assert rec["timing"] == {
        "total_time": 2.5, "working_time": 2.4,
        "started_at": "2026-11-12T10:00:00+00:00",
        "completed_at": "2026-11-12T10:00:03+00:00",
        "turn_count": 1, "retries": 0,
    }
    assert rec["wer"] == 0.2
    assert rec["flags"] == []


def test_stop_reason_falls_back_when_choices_are_empty():
    rec = sample_record(sample(output={"completion": "x", "stop_reason": "max_tokens", "choices": []}))
    assert rec["stop_reason"] == "max_tokens"
    assert "truncated" in rec["flags"]


def test_flags_mark_empty_error_and_preamble():
    rec = sample_record(sample(
        output={"completion": "  ", "choices": []},
        error={"message": "boom"},
        scores={"asr_scorer": {"value": {"wer_errors": 0, "ref_words": 4, "preamble": 2}}},
    ))
    assert rec["flags"] == ["error", "empty", "preamble"]


def test_token_only_scores_have_no_wer():
    scores = {"chunked_asr_scorer": {"value": {"hyp_tokens": 12, "ref_tokens": 14}}}
    assert per_sample_wer(scores) is None
    rec = sample_record(sample(scores=scores))
    assert rec["wer"] is None
    assert "worst" not in sorts_for([rec])


def test_zero_reference_words_have_no_wer():
    assert per_sample_wer({"asr_scorer": {"value": {"wer_errors": 0, "ref_words": 0}}}) is None


def test_sorts_and_select():
    good = sample_record(sample())
    bad = sample_record(sample(id="s2", target="other", scores={
        "asr_scorer": {"value": {"wer_errors": 9, "ref_words": 10, "preamble": 0}, "answer": "wrong"}}))
    slow = sample_record(sample(id="s3", total_time=99.0))
    rows = [good, bad, slow]
    assert sorts_for(rows) == ["order", "worst", "slowest", "longest"]
    assert [r["id"] for r in select(rows, sort="worst")] == ["s2", "s1", "s3"]
    assert [r["id"] for r in select(rows, sort="slowest")] == ["s3", "s1", "s2"]
    assert [r["id"] for r in select(rows, q="WRONG")] == ["s2"]
    assert [r["id"] for r in select(rows, issues_only=True)] == []
    assert [r["id"] for r in select(rows, sort="nonsense")] == ["s1", "s2", "s3"]


def test_context_file_honours_the_roots(tmp_path):
    inside = tmp_path / "instructions" / "a.txt"
    inside.parent.mkdir()
    inside.write_text("Transcribe this {lang} audio: ")
    assert context_file(str(inside), [tmp_path]) == {
        "path": str(inside), "exists": True,
        "text": "Transcribe this {lang} audio: ", "note": "",
    }
    outside = context_file("/etc/hostname", [tmp_path])
    assert outside["exists"] is True and outside["text"] is None
    assert outside["note"] == "outside the configured roots"
    missing = context_file(str(tmp_path / "gone.txt"), [tmp_path])
    assert missing["exists"] is False and missing["text"] is None
    relative = context_file("configs/x.yaml", [tmp_path])
    assert relative["text"] is None and relative["exists"] is None
    assert context_file(None, [tmp_path]) is None


def test_overview_lifts_setup_and_generate_config(tmp_path):
    body = {
        "eval": {
            "task": "speech-asr-clean",
            "model": "smurf//ckpt",
            "task_args": {"prompt_style": "smurf", "instruction_file": str(tmp_path / "gone.txt")},
            "model_args": {"batch_size": 8},
            "model_generate_config": {},
            "config": {"epochs": 1},
            "packages": {"inspect_ai": "0.3.254"},
            "dataset": {"name": "frozen", "location": "/frozen/manifest.jsonl", "samples": 2000},
            "scorers": [{"name": "asr_scorer", "metrics": [{"name": "corpus_wer_raw"}]}],
        },
        "results": {"scores": [{"name": "asr_scorer", "metrics": {"corpus_wer_raw": {"value": 0.1}}}]},
        "stats": {"started_at": "a", "completed_at": "b", "model_usage": {}},
        "samples": [{"events": [{"event": "model", "config": {"max_tokens": 256}}]}],
    }
    ov = overview_of(body, [tmp_path])
    assert ov["task"] == "speech-asr-clean"
    assert ov["generate_config"] == {"max_tokens": 256}
    assert ov["packages"] == {"inspect_ai": "0.3.254"}
    assert ov["dataset"]["samples"] == 2000
    assert ov["scorers"] == [{"name": "asr_scorer", "metrics": ["corpus_wer_raw"]}]
    assert ov["context_files"]["instruction_file"]["exists"] is False
    assert ov["context_files"]["system_prompt_file"] is None


def test_digest_is_written_and_reused(tmp_path):
    log = make_sample_log(tmp_path, samples=3)
    stat = log.stat()
    first = load_digest(str(log), stat.st_mtime_ns, stat.st_size, tmp_path / "s", [tmp_path])
    assert first.overview["task"] == "speech-asr-librispeech-validation-clean"
    assert [r["id"] for r in first.samples] == ["03-000000", "03-000001", "03-000002"]
    assert first.failed == 0 and first.flagged == 0
    assert first.sorts == ["order", "worst", "slowest", "longest"]
    sidecars = list((tmp_path / "s").glob("*.jsonl"))
    assert len(sidecars) == 1 and f"-v{SCHEMA_VERSION}" in sidecars[0].name
    second = load_digest(str(log), stat.st_mtime_ns, stat.st_size, tmp_path / "s", [tmp_path])
    assert second.samples == first.samples


def test_new_fingerprint_rebuilds_and_drops_the_old_sidecar(tmp_path):
    log = make_sample_log(tmp_path, samples=2)
    first = log.stat()
    load_digest(str(log), first.st_mtime_ns, first.st_size, tmp_path / "s", [tmp_path])
    load_digest(str(log), first.st_mtime_ns, first.st_size + 1, tmp_path / "s", [tmp_path])
    assert len(list((tmp_path / "s").glob("*.jsonl"))) == 1


def test_missing_and_broken_logs_raise_typed_errors(tmp_path):
    missing = tmp_path / "gone.json"
    with pytest.raises(DigestError) as err:
        load_digest(str(missing), 1, 1, tmp_path / "s", [tmp_path])
    assert err.value.status == 404
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    stat = broken.stat()
    with pytest.raises(DigestError) as err:
        load_digest(str(broken), stat.st_mtime_ns, stat.st_size, tmp_path / "s", [tmp_path])
    assert err.value.status == 409 and "json" in err.value.message.lower()


def test_failed_run_with_no_samples(tmp_path):
    log = make_sample_log(tmp_path, samples=0)
    stat = log.stat()
    digest = load_digest(str(log), stat.st_mtime_ns, stat.st_size, tmp_path / "s", [tmp_path])
    assert digest.samples == [] and digest.overview["dataset"]["samples"] == 0


def test_prune_keeps_the_newest(tmp_path):
    for i in range(3):
        path = tmp_path / f"x{i}.jsonl"
        path.write_text("x" * 10)
        os.utime(path, (1000 + i, 1000 + i))
    removed = prune(tmp_path, budget_bytes=15)
    assert removed == 2
    assert [p.name for p in tmp_path.glob("*.jsonl")] == ["x2.jsonl"]
