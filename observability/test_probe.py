"""The probe, checked against a server that lies to it.

A monitor nobody has seen fail is a monitor nobody should believe. Every check
in probe.py exists to catch one specific thing, so each of those things is
served here on purpose and the probe has to notice. The green run against the
real deployment proves it can say yes; this proves it can say no, which is the
half that matters at 3am.

    python observability/test_probe.py
"""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import probe

# What the stub returns next. Each test sets it, so the tests read as
# "when the service says this, the probe says that".
RESPONSE = {"code": 200, "body": "{}", "delay": 0.0}


class Stub(BaseHTTPRequestHandler):
    def do_GET(self):
        if RESPONSE["delay"]:
            time.sleep(RESPONSE["delay"])
        body = RESPONSE["body"].encode()
        self.send_response(RESPONSE["code"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        """The timeout test walks away mid-response, on purpose.

        The stub's traceback for the aborted write is the expected outcome of
        that test, and printing it trains whoever runs this to skim past real
        ones.
        """


def named(name):
    return next(c for c in probe.CHECKS if c.name == name)


class ProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = QuietServer(("127.0.0.1", 0), Stub)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        RESPONSE.update(code=200, body="{}", delay=0.0)

    def run_check(self, name):
        return named(name).run(self.base)

    # -- the checks say yes when they should --------------------------------

    def test_a_good_answer_passes(self):
        RESPONSE["body"] = json.dumps(
            {"outcome": "answer", "sources": [{"source": "enrollment.md", "score": 0.7}]})
        up, _, reason = self.run_check("search_hit")
        self.assertEqual((up, reason), (1, "ok"))

    def test_a_refusal_passes(self):
        RESPONSE["body"] = json.dumps({"outcome": "restricted", "sources": []})
        up, _, reason = self.run_check("search_refused")
        self.assertEqual((up, reason), (1, "ok"))

    # -- and no when they should --------------------------------------------

    def test_the_right_answer_from_the_wrong_document_fails(self):
        """Postmortem 002's failure: confident answers out of the wrong file.

        Status 200, a real answer, a plausible score. Only the source is wrong,
        which is the whole reason the check names a document instead of
        settling for "something came back".
        """
        RESPONSE["body"] = json.dumps(
            {"outcome": "answer", "sources": [{"source": "grading.md", "score": 0.7}]})
        up, _, reason = self.run_check("search_hit")
        self.assertEqual((up, reason), (0, "content"))

    def test_an_empty_index_fails(self):
        """Every request returns 200 and nothing is ever found."""
        RESPONSE["body"] = json.dumps({"outcome": "no_match", "sources": []})
        up, _, reason = self.run_check("search_hit")
        self.assertEqual((up, reason), (0, "content"))

    def test_a_leak_fails_even_though_the_refusal_looks_right(self):
        """The failure this project exists to prevent, wearing a pass.

        The outcome says "restricted" and the sources are empty, so both
        assertions the refusal check makes about structure are satisfied. The
        body describes the document anyway. Nothing server-side would notice:
        200, fast, correctly labelled.
        """
        RESPONSE["body"] = json.dumps(
            {"outcome": "restricted", "sources": [],
             "passage": "That is in the faculty compensation bands document."})
        up, _, reason = self.run_check("search_refused")
        self.assertEqual((up, reason), (0, "leak"))

    def test_a_service_that_stopped_asking_for_a_token_fails(self):
        RESPONSE["code"] = 200
        up, _, reason = self.run_check("unauthenticated")
        self.assertEqual((up, reason), (0, "status"))

    def test_a_server_error_fails(self):
        RESPONSE["code"] = 500
        up, _, reason = self.run_check("health")
        self.assertEqual((up, reason), (0, "status"))

    def test_html_where_json_was_expected_fails(self):
        """The shape of the /healthz incident: a platform 404 page, not the app."""
        RESPONSE["body"] = "<html><title>404</title></html>"
        up, _, reason = self.run_check("corpus")
        self.assertEqual((up, reason), (0, "content"))

    def test_a_hang_is_a_timeout_not_a_connection_failure(self):
        """Up and unusable reads differently from down, and must report so."""
        original, probe.TIMEOUT = probe.TIMEOUT, 0.4
        RESPONSE["delay"] = 2.0
        try:
            up, took, reason = self.run_check("health")
        finally:
            probe.TIMEOUT = original
        self.assertEqual((up, reason), (0, "timeout"))
        self.assertLess(took, 1.5)

    def test_nothing_listening_is_a_connection_failure(self):
        with QuietServer(("127.0.0.1", 0), Stub) as dead:
            port = dead.server_address[1]
        up, _, reason = named("health").run(f"http://127.0.0.1:{port}")
        self.assertEqual((up, reason), (0, "connection"))

    # -- what gets shipped --------------------------------------------------

    def test_line_protocol_is_one_point_per_check_sharing_a_timestamp(self):
        lines, failed = probe.measure(self.base, "test")
        self.assertEqual(len(lines), len(probe.CHECKS))
        stamps = {line.rsplit(" ", 1)[1] for line in lines}
        self.assertEqual(len(stamps), 1, "points must line up on the dashboard")
        for line in lines:
            self.assertRegex(line, r"^kb_probe,check=\w+,origin=test,reason=\w+ "
                                   r"up=[01]i,duration_seconds=\d+\.\d+ \d+$")
        self.assertTrue(failed, "the stub answers {} to everything")


if __name__ == "__main__":
    unittest.main(verbosity=2)
