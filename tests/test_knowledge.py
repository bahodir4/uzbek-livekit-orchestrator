import asyncio
import tempfile
from pathlib import Path
import unittest
from voice_agent.knowledge import LocalKnowledgeStore

class TestKnowledge(unittest.TestCase):
    def test_bounded_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'product.md').write_text('Buyurtmani qaytarish muddati yetti kun.', encoding='utf8')
            store = LocalKnowledgeStore(Path(tmp))
            result = asyncio.run(store.search('buyurtmani qaytarish mumkinmi?'))
            self.assertIn('yetti kun', result)

    def test_missing(self):
        store = LocalKnowledgeStore(Path('/definitely/not/there'))
        self.assertIn('mavjud emas', asyncio.run(store.search('savol haqida')))

if __name__ == '__main__':
    unittest.main()
