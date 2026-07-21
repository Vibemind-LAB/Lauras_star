"""A run that cannot possibly work must say so before it starts, and say what is wrong.

Live incident (2026-07-18): a production run was started against ``openai-compat`` while the
backend process had no ``LAURA_AGENT_API_KEY``. Nothing checked. The job was enqueued, a board
was created, two escalation stages were spent, and the whole thing came back as
``{"status": "hard_fail", "summary": "Connection error."}`` — a configuration mistake wearing a
transport error's clothes. It then went unnoticed for 55 minutes.

Preflight makes the distinction structural rather than diagnostic: a config that cannot reach a
model never gets as far as a model call, so "Connection error." from then on means what it says.

The silent-fallback case is the same failure one level quieter. ``LAURA_AGENT_PROVIDER=openai``
— a plausible typo for ``openai-compat`` — is not a known provider, and the resolver quietly
substitutes the ollama default. The run then works, slowly, against an entirely different
backend than the operator configured, and nothing anywhere says so.
"""

from __future__ import annotations

from laura.short_creator.providers import config_problems, config_warnings, resolve_from_env

_OPENAI = {
    "LAURA_AGENT_PROVIDER": "openai-compat",
    "LAURA_AGENT_BASE_URL": "https://api.openai.com/v1",
    "LAURA_AGENT_MODEL": "gpt-5-mini",
}


# --- the incident: a provider that needs a key, without one --------------------------------


def test_openai_compat_without_a_key_is_refused_by_name() -> None:
    problems = config_problems(resolve_from_env(_OPENAI))

    assert problems, "this is the config that burned 55 minutes"
    assert any("LAURA_AGENT_API_KEY" in p for p in problems), problems


def test_the_same_config_with_a_key_is_accepted() -> None:
    assert config_problems(resolve_from_env({**_OPENAI, "LAURA_AGENT_API_KEY": "sk-test"})) == []


def test_a_blank_key_counts_as_missing() -> None:
    """Whitespace in an .env line is not a credential."""
    problems = config_problems(resolve_from_env({**_OPENAI, "LAURA_AGENT_API_KEY": "   "}))

    assert any("LAURA_AGENT_API_KEY" in p for p in problems), problems


def test_9router_names_its_own_key() -> None:
    problems = config_problems(resolve_from_env({"LAURA_AGENT_PROVIDER": "9router"}))

    assert any("LAURA_9ROUTER_API_KEY" in p for p in problems), problems


def test_ollama_needs_no_key_at_all() -> None:
    """The zero-config local default must stay startable — that is the product's promise."""
    assert config_problems(resolve_from_env({})) == []


# --- the silent fallback --------------------------------------------------------------------


def test_a_misspelled_provider_is_reported_rather_than_substituted() -> None:
    """"openai" is not a provider; falling back to ollama runs the wrong backend in silence."""
    problems = config_problems(resolve_from_env({**_OPENAI, "LAURA_AGENT_PROVIDER": "openai"}))

    assert any("openai" in p and "ollama" in p for p in problems), problems


def test_a_known_provider_is_never_reported_as_a_fallback() -> None:
    for name in ("ollama", "9router", "openai-compat"):
        config = resolve_from_env({"LAURA_AGENT_PROVIDER": name, "LAURA_AGENT_API_KEY": "k"})
        assert not any("fell back" in p for p in config_problems(config)), name


def test_an_unset_provider_is_the_default_not_a_fallback() -> None:
    """Leaving it unset is a choice; misspelling it is a mistake. Do not conflate them."""
    assert config_problems(resolve_from_env({})) == []


# --- escalation must be reachable too -------------------------------------------------------
# A run escalates to stage B on a stage-A hard failure. If stage B's provider is unusable, the
# escalation burns a second full attempt to reach the same wall — which is what the live run did.


def test_an_unreachable_escalation_provider_is_named(monkeypatch: object) -> None:
    problems = config_problems(
        resolve_from_env(
            {
                "LAURA_AGENT_PROVIDER": "ollama",
                "LAURA_AGENT_ESCALATE_PROVIDER": "openai-compat",
                "LAURA_AGENT_AUTO_ESCALATE": "1",
            }
        )
    )

    assert any("escalat" in p.lower() for p in problems), problems


def test_escalation_is_not_checked_when_it_cannot_fire() -> None:
    """Without auto-escalation the stage-B provider is never used unasked — do not block on it."""
    problems = config_problems(
        resolve_from_env(
            {"LAURA_AGENT_PROVIDER": "ollama", "LAURA_AGENT_ESCALATE_PROVIDER": "openai-compat"}
        )
    )

    assert problems == []


# --- config_warnings: advisory, never a gate ------------------------------------------------


def test_config_warnings_flags_local_ollama_text_agents() -> None:
    """Live incident 2026-07-20: three production runs silently ran their text agents on
    qwen2.5:7b (provider default ollama) — tool calls came out as JSON prose, save_storyline
    got an invented schema, the orchestrator hallucinated "saved". One advisory line at
    enqueue time would have saved the hour."""
    config = resolve_from_env({})  # zero env -> ollama default

    warnings = config_warnings(config)

    assert len(warnings) == 1
    assert "ollama" in warnings[0]
    assert config.agent_model in warnings[0]
    assert "LAURA_AGENT_PROVIDER=openai-compat" in warnings[0]


def test_config_warnings_empty_for_hosted_providers() -> None:
    hosted = resolve_from_env(
        {"LAURA_AGENT_PROVIDER": "openai-compat", "LAURA_AGENT_API_KEY": "k"}
    )
    routed = resolve_from_env(
        {"LAURA_AGENT_PROVIDER": "9router", "LAURA_9ROUTER_API_KEY": "k"}
    )

    assert config_warnings(hosted) == []
    assert config_warnings(routed) == []
