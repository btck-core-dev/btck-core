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
        self.assertIn("server_name bitcoin-k.com;", config)
        self.assertIn("server_name www.bitcoin-k.com;", config)
        self.assertIn("return 308 https://bitcoin-k.com$request_uri;", config)
        self.assertIn("proxy_pass http://btck_private_mainnet_web/home;", config)
        self.assertIn("proxy_pass http://btck_private_mainnet_web/explorer;", config)
        self.assertGreaterEqual(config.count("allow 51.195.136.143;"), 4)
        self.assertGreaterEqual(config.count("deny all;"), 4)

    def test_certbot_hooks_scope_temporary_access_to_http_only(self):
        pre = (ROOT / "deploy" / "certbot-hooks" / "10-btck-open-acme-http.sh").read_text(encoding="utf-8")
        post = (ROOT / "deploy" / "certbot-hooks" / "90-btck-close-acme-http.sh").read_text(encoding="utf-8")
        self.assertIn("--dport 80", pre)
        self.assertIn("btck-acme-temporary", pre + post)
        self.assertNotIn("--dport 443", pre + post)
        self.assertIn("iptables -D", post)

    def test_home_and_whitepaper_are_english_and_honest_about_status(self):
        home = (ROOT / "web" / "home.html").read_text(encoding="utf-8")
        paper = (ROOT / "web" / "whitepaper.html").read_text(encoding="utf-8")
        self.assertIn("THE AGENT", home)
        self.assertIn("21,000,000 BTCK", home + paper)
        self.assertIn("PLANNED", home)
        self.assertIn("id=\"participate\"", home)
        self.assertIn("not live products", paper)
        self.assertIn("The primary mission: an open Agent economy", paper)
        self.assertIn("One Agent call, end to end", paper)
        self.assertIn("Private robot wallets", paper)
        self.assertIn("Serving GPU provider", paper)
        self.assertIn("20% and 33%", paper)
        self.assertIn("IMPLEMENTATION STATUS", paper)
        self.assertNotIn("85% direct-worker payout", paper)
        self.assertIsNone(re.search(r"[\u3400-\u9fff]", home + paper))

    def test_public_document_set_is_english_and_status_safe(self):
        names = ("participate.html", "genesis.html", "releases.html", "llms.txt")
        docs = "\n".join((ROOT / "web" / name).read_text(encoding="utf-8") for name in names)
        self.assertIsNone(re.search(r"[\u3400-\u9fff]", docs))
        self.assertIn("PUBLIC PORT CLOSED", docs)
        self.assertIn("NO PUBLIC RELEASE YET", docs)
        self.assertIn("PENDING", docs)

    def test_machine_network_record_is_valid_and_conservative(self):
        import json
        record = json.loads((ROOT / "web" / "network.json").read_text(encoding="utf-8"))
        self.assertEqual(record["network"]["chain_id"], "btck-mainnet-v1")
        self.assertEqual(record["genesis"]["hash"], "b9249be0f1b57021a9cfef8241a499c6e975a91e24d6a3fc22ce850000000000")
        self.assertFalse(record["availability"]["public_mining"])
        self.assertFalse(record["availability"]["gpu_market"])

    def test_pool_ui_never_copies_or_searches_payout_addresses(self):
        pool = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("copyText('${v.address}')", pool)
        self.assertNotIn("winner_address", pool)
        self.assertNotIn("v.address.toLowerCase()", pool)
        self.assertIn("Payout identity withheld", pool)

    def test_pool_combo_policy_states_scope_and_payout_boundaries(self):
        pool = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Official Pool Policy · Anti-Hopping", pool)
        self.assertIn("at least 95% of the epoch's slots", pool)
        self.assertIn("failing the Combo gate removes only official-pool Combo eligibility".lower(), pool.lower())
        self.assertIn("not a BlockDAG consensus rule", pool)
        self.assertIn("Private Stratum shares never enter L1 winner selection", pool)
        self.assertIn("no qualified pool candidate", pool)
        self.assertIn("no fixed return or prize multiple is promised", pool)
        self.assertNotIn("hashrate works for free", pool.lower())
        self.assertNotIn("guaranteed profit", pool.lower())


if __name__ == "__main__":
    unittest.main()
