"""Whitelisted agents/languages, built from the agents/ folder (see agent_registry). Older persona ids are mapped to a
current agent so stored preferences and conversations keep working."""
from __future__ import annotations

from . import agent_registry

_registry = agent_registry.get()
DEFAULT_PERSONA = _registry.default
PERSONAS = {
    spec.id: {'id': spec.id, 'name': spec.name, 'role': spec.role, 'voice_gender': spec.voice_gender,
              'tasks': list(spec.tasks), 'greeting': spec.greeting, 'instructions': spec.instructions}
    for spec in _registry.agents.values()
}
LEGACY_PERSONAS = dict(_registry.legacy_aliases)
PERSONA_IDS = tuple(PERSONAS) + tuple(LEGACY_PERSONAS)
LANGUAGES = {'uz': 'Uzbek', 'ru': 'Russian', 'en': 'English'}
MODELS = {'sayro', 'navoiy'}


def canonical_persona(persona: str | None) -> str:
    """Current agent id for a current or legacy id; unknown values fall back to the default agent."""
    if persona in PERSONAS:
        return persona
    return LEGACY_PERSONAS.get(persona or '', DEFAULT_PERSONA)


def check_preferences(persona: str, language: str, model: str):
    if persona not in PERSONA_IDS: raise ValueError('Unknown persona')
    if language not in LANGUAGES: raise ValueError('Unknown language')
    if model not in MODELS: raise ValueError('Unknown TTS model')


def agent_instructions(persona: str, language: str, original: str) -> str:
    check_preferences(persona, language, 'navoiy')
    return '\n'.join((original, PERSONAS[canonical_persona(persona)]['instructions'],
                      _registry.locale_instructions[language], _registry.guard_instruction))
