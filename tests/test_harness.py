"""The fake sandbox itself: the other tests rely on it."""
import os
import unittest

from harness import AiTestCase, q


class FakeSandbox(AiTestCase):
    def test_a_session_runs_on_the_host_with_the_network_set_up(self):
        r = self.ai("-w", session='touch "$WORK/ran"; test -d "$LAYER"; test -p "$NOTIFY"')
        self.assertOk(r)
        self.assertTrue((self.work / "ran").exists())
        # Released only once the firewall and slirp4netns were ready.
        self.assertIn("nft.rules", r.bwrap.at_release)
        self.assertIn("slirp.args", r.bwrap.at_release)

    def test_q_gives_any_name_to_a_session(self):
        for name in ("a\tb", "a\nb", os.fsdecode(b"a\xffb"), "$(x)", "-rf"):
            with self.subTest(name=name):
                r = self.ai("-w", session=f"touch -- {q(name)}")
                self.assertOk(r)
                self.assertTrue((self.work / name).exists())
                (self.work / name).unlink()

    def test_questions_are_answered_and_default_to_no(self):
        self.make_repo()
        r = self.ai("-w", answers=["y"])
        self.assertOk(r)
        self.assertIn("did you create it yourself?", r.err)
        self.make_repo(self.work / "sub")
        r = self.ai("-w")
        self.assertRefused(r, "Not trusting")

    def test_nothing_happens_in_the_real_home(self):
        r = self.ai("-w")
        self.assertOk(r)
        self.assertEqual(r.bwrap.env()["HOME"], str(self.home))


if __name__ == "__main__":
    unittest.main()
