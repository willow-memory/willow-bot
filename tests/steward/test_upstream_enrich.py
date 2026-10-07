from willow_bot.steward.upstream_desk import _enrich_open_prs


def test_enrich_buckets_tier_a_and_stale() -> None:
    prs = [
        {
            "repo": "DeusData/codebase-memory-mcp",
            "number": 1702,
            "title": "fix registry",
            "url": "https://example/1702",
            "updatedAt": "2026-08-01T00:00:00Z",
            "isDraft": False,
        },
        {
            "repo": "rudi193-cmd/willow-seed",
            "number": 3,
            "title": "DO NOT MERGE seed",
            "url": "https://example/3",
            "updatedAt": "2026-07-10T00:00:00Z",
            "isDraft": True,
        },
    ]
    watch = ["DeusData/codebase-memory-mcp"]
    enriched, summary = _enrich_open_prs(prs, watch, "rudi193-cmd")
    assert enriched[0]["lane"] == "tier_a"
    assert enriched[0]["stale"] is True
    assert enriched[1]["lane"] == "hold"
    assert summary["tier_a_count"] == 1
    assert summary["hold_count"] == 1
    assert summary["stale_count"] == 1
