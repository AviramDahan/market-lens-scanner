from app import smart_universe as universe


def test_larger_request_widens_source_pool_without_admitting_weak_sector(monkeypatch):
    monkeypatch.setattr(universe, "curated_universe", lambda: {})
    source = {
        **{f"TECH{i:03d}": "Technology" for i in range(100)},
        **{f"FIN{i:03d}": "Financials" for i in range(100)},
        **{f"WEAK{i:03d}": "Energy" for i in range(100)},
    }
    health = {
        "Technology": {"label": "Strong"},
        "Financials": {"label": "Strong"},
        "Energy": {"label": "Weak"},
    }
    small = universe.candidate_pool(source, health, requested=35)
    large = universe.candidate_pool(source, health, requested=175)
    assert len(large) > len(small)
    assert all(sector != "Energy" for sector in large.values())
    assert len(large) <= 240


def test_stale_marsh_symbol_is_normalized_at_source_boundary():
    merged = {}
    universe.merge_universe_source(
        merged,
        {"MMC": {"name": "Marsh & McLennan", "sector": "Financials"}},
        "upstream", "", [], [],
    )
    assert "MMC" not in merged
    assert merged["MRSH"]["sector"] == "Financials"
