// k6 run perf/latency.js  --env BASE=http://localhost:8080 --env TOKEN=devstudent
//
// The budget is in `thresholds`, and a breached threshold makes k6 exit 99,
// which fails the build. That is the entire point: a load test that prints a
// number nobody blocks on is a number nobody reads.
//
// Only /search is measured, and only with questions whose embeddings are
// already cached. That sounds like cheating and is the opposite: it isolates
// the part of the latency this repository controls. A cold question spends most
// of its time inside somebody else's embedding API, so including it would build
// a budget that fails when a vendor has a slow afternoon, and a gate that fails
// for reasons the team cannot fix gets disabled. Layer 5 measures the rest.
import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE || 'http://localhost:8080';
const TOKEN = __ENV.TOKEN || 'devstudent';

// Read from a file rather than typed here, and test_serve.py reads the same
// file and asserts every question in it has a cached vector. Typed inline, two
// of the six were questions nobody had ever embedded, which in a container with
// no API key is not a slow response but a 500 -- and a load test that measures
// the latency of an error message will happily stay green.
const QUESTIONS = JSON.parse(open('./questions.json'));

export const options = {
  scenarios: {
    steady: {
      executor: 'constant-vus',
      vus: 10,
      duration: '30s',
    },
  },
  thresholds: {
    // Measured at 24ms p95 on a developer laptop at this concurrency. The
    // budget is an order of magnitude above that, which is deliberate: a CI
    // runner is a noisy shared machine, and a budget tuned to a quiet laptop
    // fails on Tuesdays and teaches everyone to re-run the job. This catches
    // the regressions worth catching -- an accidental index rebuild per
    // request, a synchronous embed on the hot path -- and not the weather.
    'http_req_duration{expected_response:true}': ['p(95)<250'],
    // A fast error is still an error, and p95 alone cannot see it.
    'http_req_failed': ['rate<0.01'],
    'checks': ['rate>0.99'],
  },
};

export default function () {
  const q = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
  const res = http.get(`${BASE}/search?q=${encodeURIComponent(q)}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  check(res, {
    'status 200': (r) => r.status === 200,
    // A 200 with an empty body would sail past a latency check while serving
    // nothing, which is the failure mode a load test is most likely to miss.
    'answered as student': (r) => r.json('role') === 'student',
    'body not empty': (r) => (r.json('result') || '').length > 20,
  });
}
