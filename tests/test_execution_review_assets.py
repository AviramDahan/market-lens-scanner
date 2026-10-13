import re
from pathlib import Path


def test_execution_review_loads_assets_with_cache_keys_newer_than_old_dashboard():
    root=Path(__file__).resolve().parents[1]/'app/static'
    html=(root/'agent.html').read_text(encoding='utf-8')
    urls=re.findall(r'/static/agent\.(?:js|css)\?v=([^"\s]+)',html)
    assert len(urls)==2 and all(v!='20260923-partial-scan-status' for v in urls)
    assert 'executionReviewNotice' in html
    assert 'Historical execution review' in (root/'agent.js').read_text(encoding='utf-8')
    assert '.execution-review' in (root/'agent.css').read_text(encoding='utf-8')
