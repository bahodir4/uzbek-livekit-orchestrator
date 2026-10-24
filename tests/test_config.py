from pathlib import Path
import copy
import os
import unittest
import tempfile

from voice_agent.configuration import load, validate, save_atomic, active_spec
from voice_agent.cli import set_active, tune, add_provider


class TestConfig(unittest.TestCase):
    def test_valid_default(self):
        data = load()
        self.assertEqual(data['active']['stt'], 'gigaam_primary')
        self.assertEqual(active_spec(data, 'tts')['type'], 'dual_gateway_stream')

    def test_secrets_cannot_be_committed_in_yaml(self):
        data = copy.deepcopy(load())
        data['providers']['stt']['gigaam_primary']['api_key'] = 'secret'
        with self.assertRaisesRegex(ValueError, 'Do not store secrets'):
            validate(data)

    def test_invalid_endpointing_rejected(self):
        data = copy.deepcopy(load())
        data['turn']['endpointing_min_delay'] = 20
        with self.assertRaises(ValueError):
            validate(data)

    def test_finite_registry(self):
        data = copy.deepcopy(load())
        data['providers']['tts']['sayro_primary']['type'] = 'import_python'
        with self.assertRaises(ValueError):
            validate(data)

    def test_atomic_edit_and_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            import yaml
            path = Path(tmp) / 'agent.yaml'
            save_atomic(load(), path)
            os.environ['AGENT_CONFIG_PATH'] = str(path)
            try:
                add_provider('llm', 'backup_llm', 'openai_compatible', 'http://localhost:9000/v1', 'ALT_KEY', 'alternate')
                set_active('llm', 'backup_llm')
                tune('turn.endpointing_min_delay', '0.25')
                data = load()
                self.assertEqual(data['active']['llm'], 'backup_llm')
                self.assertEqual(data['turn']['endpointing_min_delay'], 0.25)
            finally:
                os.environ.pop('AGENT_CONFIG_PATH', None)


if __name__ == '__main__':
    unittest.main()

class TestProviderSet(unittest.TestCase):
    def test_set_provider(self):
        import tempfile
        from voice_agent.cli import set_provider_field
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'agent.yaml'
            save_atomic(load(), path)
            os.environ['AGENT_CONFIG_PATH'] = str(path)
            try:
                set_provider_field('tts', 'sayro_primary', 'max_new_tokens', '160')
                self.assertEqual(load()['providers']['tts']['sayro_primary']['max_new_tokens'], 160)
                with self.assertRaises(ValueError):
                    set_provider_field('tts', 'sayro_primary', 'api_key', 'BAD')
            finally:
                os.environ.pop('AGENT_CONFIG_PATH', None)
