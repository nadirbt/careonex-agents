from careonex_retrieve import evaluate as ev


def test_evaluation_reports_failed_evidence_even_with_nonempty_hits(monkeypatch):
    monkeypatch.setattr(ev, "CASES", (ev.Case("test", "find JACC", "JACC", "$4,855"),))
    monkeypatch.setattr(ev, "_fetch", lambda *_: {"latency_ms": 1, "passages": [
        {"text": "JACC 2025 $4,760", "source_url": "https://www.nj.gov/test", "title": "NJ DoAS"}
    ]})
    report = ev.evaluate("http://localhost:8080", repeats=3)
    assert report["checks"] == 3 and report["evidence_hit_rate"] == 0
    assert report["traceable_rate"] == 1 and not report["all_passed"]


def test_evaluation_does_not_hide_unreachable_api(monkeypatch):
    import urllib.error

    monkeypatch.setattr(ev, "CASES", (ev.Case("test", "q", "JACC", "limit"),))
    def fail(*args):
        raise urllib.error.URLError("no server")
    monkeypatch.setattr(ev, "_fetch", fail)
    report = ev.evaluate("http://localhost:8080", repeats=1)
    assert report["results"][0]["error"].startswith("URLError")
    assert not report["all_passed"]
