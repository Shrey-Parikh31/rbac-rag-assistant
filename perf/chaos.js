// Steady traffic through the Service while something is broken on purpose.
//
// Runs as a pod inside the cluster, so every request goes through the real
// Service and the real kube-proxy -- which is where a dying pod is supposed to
// be taken out of rotation, and where the experiment is interested in whether it
// is taken out fast enough. From outside via port-forward, the traffic would be
// tunnelled to a single pod and the experiment would measure the tunnel.
//
// Any failed request fails the run. The question is not "is it mostly fine" but
// "does replacing a pod cost a user anything", and one failure is an answer.
import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE || 'http://kb';
const TOKEN = __ENV.TOKEN || 'devstudent';
const QUESTIONS = JSON.parse(open('./questions.json'));

export const options = {
  scenarios: {
    // 75s, not 40. The first run lasted 40s with the kill at 15s and passed
    // cleanly -- because Python as PID 1 ignored SIGTERM, Kubernetes waited
    // its 30s grace period before SIGKILL, and 15 + 30 is after 40. The
    // experiment ended before the failure it existed to observe. The window
    // now has to outlast the grace period, or it measures nothing.
    steady: { executor: 'constant-vus', vus: 10, duration: '75s' },
  },
  thresholds: {
    http_req_failed: ['rate==0'],
  },
};

export default function () {
  const q = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
  const res = http.get(`${BASE}/search?q=${encodeURIComponent(q)}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    timeout: '5s',
  });
  check(res, { 'status 200': (r) => r.status === 200 });
}
