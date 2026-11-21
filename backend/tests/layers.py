"""Test-layer classification for files that predate tests/unit, tests/module and tests/integration.

New tests go in the layer directory instead; the directory decides the marker.
See docs/TESTING.md for the definitions.
"""

UNIT = "unit"
MODULE = "module"
INTEGRATION = "integration"
LAYERS = (UNIT, MODULE, INTEGRATION)

# Legacy top-level files, classified without moving them. Files that drive the
# FastAPI app against SQLite or fakes are "module": integration means the real
# app plus a real PostgreSQL database.
LEGACY_LAYERS: dict[str, str] = {
    "test_adapter.py": UNIT,
    "test_auth.py": UNIT,
    "test_demo_commerce.py": UNIT,
    "test_instamart_workflows.py": UNIT,
    "test_logging.py": UNIT,
    "test_memory_extraction.py": UNIT,
    "test_offer_suggestions.py": UNIT,
    "test_product_relevance.py": UNIT,
    "test_purchase_patterns.py": UNIT,
    "test_query_guard.py": UNIT,
    "test_realtime_voice_evaluation.py": UNIT,
    "test_recommender.py": UNIT,
    "test_relevance_benchmark.py": UNIT,
    "test_settings.py": UNIT,
    "test_swiggy_history.py": UNIT,
    "test_voice_diversity.py": UNIT,
    "test_voice_prompt_evaluation.py": UNIT,
    "test_zepto_cart.py": UNIT,
    "test_zepto_catalogue_fixture.py": UNIT,
    "test_zepto_history.py": UNIT,
    "test_zepto_tool_policy.py": UNIT,
    "test_api.py": MODULE,
    "test_approval.py": MODULE,
    "test_basket.py": MODULE,
    "test_capabilities.py": MODULE,
    "test_checkout_safety.py": MODULE,
    "test_conversations.py": MODULE,
    "test_jev_reranker.py": MODULE,
    "test_jev_resilience.py": MODULE,
    "test_legal_consent.py": MODULE,
    "test_local_secrets.py": MODULE,
    "test_memory.py": MODULE,
    "test_oauth.py": MODULE,
    "test_oauth_readiness.py": MODULE,
    "test_provider_route_auth.py": MODULE,
    "test_purchase_graph.py": MODULE,
    "test_shopping_database.py": MODULE,
    "test_support.py": MODULE,
    "test_swiggy_quote_provider.py": MODULE,
    "test_swiggy_rate_limit.py": MODULE,
    "test_swiggy_retry_safety.py": MODULE,
    "test_swiggy_service.py": MODULE,
    "test_swiggy_sessions.py": MODULE,
    "test_tracking.py": MODULE,
    "test_tracking_safety.py": MODULE,
    "test_voice.py": MODULE,
    "test_voice_address_form.py": MODULE,
    "test_voice_basket_tools.py": MODULE,
    "test_voice_tools.py": MODULE,
    "test_zepto.py": MODULE,
    "test_zepto_checkout.py": MODULE,
    "test_zepto_checkout_state.py": MODULE,
    "test_zepto_purchase_history.py": MODULE,
    "test_zepto_read_api.py": MODULE,
    "test_zepto_safety.py": MODULE,
    "test_zepto_search.py": MODULE,
    "test_zepto_sessions.py": MODULE,
}
