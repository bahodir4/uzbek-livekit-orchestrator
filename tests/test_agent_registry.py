"""agents/ folder: valid files, nothing hard-coded in code, and clear errors for broken files."""
import re
import shutil
from pathlib import Path

import pytest
import yaml

from voice_agent import agent_registry as R, agent_tools

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_files_load_and_render():
    reg = R.load()
    assert list(reg.agents) == ['sofiya', 'malika', 'dilnoza', 'zarina'] and reg.default == 'sofiya'
    for spec in reg.agents.values():
        assert spec.voice_gender == 'female'
        assert '{' not in spec.instructions and '{' not in spec.greeting        # every placeholder was filled
        for other in reg.agents.values():                                       # shared rules list every agent
            assert other.name in spec.instructions
    assert '09:00-17:00' in reg.tool_descriptions['available_slots']             # built from settings.yaml


def test_every_tool_named_in_files_has_code_and_every_code_tool_is_used():
    reg = R.load()
    used = {t for spec in reg.agents.values() for t in spec.tools}
    assert used <= set(agent_tools.IMPLEMENTATIONS)
    assert set(agent_tools.IMPLEMENTATIONS) <= set(reg.tool_descriptions)
    assert used == set(agent_tools.IMPLEMENTATIONS)


def test_every_message_key_used_in_code_exists_in_messages_yaml():
    reg = R.load()
    source = (ROOT / 'voice_agent' / 'agent_tools.py').read_text()
    keys = set(re.findall(r'text\(\s*"([a-z_]+\.[a-z_]+)"', source))
    keys |= set(re.findall(r'"((?:results|errors|validation)\.[a-z_]+)"', source))
    assert keys, 'expected message keys in agent_tools.py'
    for key in keys:
        node = reg.messages
        for part in key.split('.'):
            assert part in node, f'messages.yaml is missing {key}'
            node = node[part]
    for field in re.findall(r'_check\([^,]+,\s*"([a-z_]+)"', source):
        assert field in reg.messages['fields'], f'messages.yaml fields is missing {field}'


def test_no_agent_prompt_or_spoken_text_is_hard_coded_in_python():
    for name in ('agent_tools.py', 'experience.py'):
        source = (ROOT / 'voice_agent' / name).read_text()
        assert 'Assalomu' not in source and 'Sizning ismingiz' not in source and 'Kechirasiz' not in source


@pytest.fixture
def copy(tmp_path):
    target = tmp_path / 'agents'
    shutil.copytree(ROOT / 'agents', target)
    return target


def test_changing_a_file_changes_the_agent_without_touching_code(copy):
    path = copy / 'sofiya.yaml'
    data = yaml.safe_load(path.read_text())
    data['greeting'] = 'Salom! Men {name}.'
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    assert R.load(copy).agents['sofiya'].greeting == 'Salom! Men Sofiya.'


@pytest.mark.parametrize('break_it, message', [
    (lambda d: (d / 'dilnoza.yaml').write_text((d / 'dilnoza.yaml').read_text().replace('available_slots', 'no_such_tool', 1)),
     "tool 'no_such_tool'"),
    (lambda d: (d / 'malika.yaml').write_text((d / 'malika.yaml').read_text().replace('{name}', '{nme}', 1)), 'unknown placeholder'),
    (lambda d: (d / 'index.yaml').write_text((d / 'index.yaml').read_text().replace('default: sofiya', 'default: nobody')), 'default agent'),
    (lambda d: (d / 'zarina.yaml').unlink(), 'cannot read'),
    (lambda d: (d / 'messages.yaml').write_text('results: [unterminated'), 'not valid YAML'),
    (lambda d: (d / 'sofiya.yaml').write_text((d / 'sofiya.yaml').read_text().replace('female', 'robot')), 'voice_gender'),
])
def test_broken_files_fail_with_a_clear_message(copy, break_it, message):
    break_it(copy)
    with pytest.raises(R.AgentConfigError, match=message):
        R.load(copy)


def test_business_rules_come_from_settings(copy, monkeypatch):
    path = copy / 'settings.yaml'
    data = yaml.safe_load(path.read_text())
    data['appointments'].update(day_start='10:00', day_end='12:00', slot_minutes=60, breaks=[], capacity_per_slot=1)
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    monkeypatch.setenv('AGENTS_DIR', str(copy))
    R.get.cache_clear()
    try:
        assert agent_tools.slot_times() == ['10:00', '11:00']
        assert agent_tools._reg().tool_descriptions['available_slots'].count('10:00-12:00') == 1
    finally:
        monkeypatch.delenv('AGENTS_DIR')
        R.get.cache_clear()
