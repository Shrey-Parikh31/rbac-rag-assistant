"""Turn every query in every dashboard into a rule file, so promtool can judge it.

A dashboard panel with a typo in its query does not fail. It renders an empty
graph that says "No data", which is also what a healthy quiet service renders,
and the two are indistinguishable at a glance. Nobody finds out until the
morning somebody opens the dashboard during an incident and it tells them
nothing.

Prometheus has a parser and CI already runs it for the alert rules, so the
queries get held to the same standard: the same job, the same binary, on every
push. The rule file this writes is thrown away; only the exit code matters.

    python observability/check_dashboards.py > queries.yaml
    promtool check rules queries.yaml

It checks syntax, not meaning. A query that parses and names a metric nothing
emits still draws an empty graph, which is why the metric names below are
checked against what serve.py and probe.py actually produce.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Every metric a dashboard is allowed to name. Anything else is either a typo or
# a metric somebody meant to add and did not, and both draw the same empty graph.
KNOWN = {
    # serve.py, Metrics.render() and gauges()
    "kb_requests_total",
    "kb_request_duration_seconds_bucket",
    "kb_request_duration_seconds_sum",
    "kb_request_duration_seconds_count",
    "kb_search_outcomes_total",
    "kb_breaker_state",
    "kb_tokens_version",
    # observability/probe.py, via the Influx endpoint's measurement_field naming
    "kb_probe_up",
    "kb_probe_duration_seconds",
    # Prometheus itself
    "up",
}

METRIC = re.compile(r"\b([a-z_][a-z0-9_]*)\s*(?:\{|\[|\)|\s|$)")
FUNCTIONS = set("""
sum rate avg min max count by without on ignoring group_left group_right and or
unless increase irate histogram_quantile avg_over_time min_over_time
max_over_time count_over_time sum_over_time last_over_time present_over_time
stddev stdvar topk bottomk quantile absent absent_over_time delta idelta
clamp_max clamp_min round abs ceil floor exp ln log2 log10 sqrt time
offset le instance pod job route code outcome check origin reason bool
""".split())


def queries():
    for path in sorted(glob.glob(os.path.join(HERE, "dashboards", "*.json"))):
        board = json.load(open(path, encoding="utf-8"))
        for panel in board["panels"]:
            for t in panel.get("targets", []):
                yield os.path.basename(path), panel["title"], t["expr"]


def main():
    lines = ["groups:", "  - name: dashboard-queries", "    rules:"]
    unknown = []
    for n, (path, title, expr) in enumerate(queries()):
        for name in METRIC.findall(expr):
            if name not in KNOWN and name not in FUNCTIONS:
                unknown.append(f"{path}: {title!r}: no metric named {name!r}")
        lines.append(f"      - record: q:n{n}")
        lines.append(f"        # {path}: {title}")
        lines.append("        expr: |")
        lines += ["          " + line for line in expr.splitlines()]

    if unknown:
        print("\n".join(unknown), file=sys.stderr)
        print(f"\n{len(unknown)} query(ies) name a metric nothing emits. Either the "
              f"name is a typo, or the metric needs adding to serve.py, or it "
              f"belongs in KNOWN in this file.", file=sys.stderr)
        return 1

    print("\n".join(lines))
    print(f"{n + 1} queries from {len(set(p for p, _, _ in queries()))} dashboards",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
