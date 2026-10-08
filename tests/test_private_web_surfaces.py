import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestPrivateWebSurfaces(unittest.TestCase):
    def test_explorer_and_pool_are_distinct_english_pages(self):
        explorer = (ROOT / "web" / "explorer.html").read_text(encoding="utf-8")
        pool = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("<title>BTC-K BlockDAG Explorer</title>", explorer)
        self.assertIn("<title>BTC-K Mining Pool</title>", pool)
        self.assertNotEqual(explorer, pool)
        self.assertIsNone(re.search(r"[\u3400-\u9fff]", explorer + pool))

    def test_private_nginx_routes_explorer_root_separately(self):
        config = (ROOT / "deploy" / "public-edge" / "guardian-private-sites.nginx.conf.template").read_text(encoding="utf-8")
        self.assertIn("server_name explorer.bitcoin-k.com;", config)
        self.assertIn("server_name pool.bitcoin-k.com;", config)
        self.assertIn("proxy_pass http://btck_private_mainnet_web/explorer;", config)
        self.assertGreaterEqual(config.count("allow 51.195.136.143;"), 3)
        self.assertGreaterEqual(config.count("deny all;"), 3)


if __name__ == "__main__":
    unittest.main()
