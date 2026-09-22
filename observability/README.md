# Observability

Four experiments in `reliability/` broke this service on purpose. Three of the
four failures they found were invisible to every metric the service collected
about itself, and that is the problem this directory exists to solve.

| The failure | What the server reported |
|---|---|
| 1 in 5 connections refused before the process saw them | 100% availability |
| one pod at its CPU limit, its replacement serving nothing | 100% availability, no errors |
| the wrong document returned for half of all questions | 200 OK, fast |
| a revoked token still working on one pod | 200 OK, to the wrong person |

None of those are subtle once you know to look. All four return success. **A
system can fail completely in ways that never produce an error**, and an error
ratio is the metric most monitoring starts and ends with.

## Two sources, because one of them cannot see enough

```
                    the deployment                       somewhere else
   ┌──────────────────────────────────────┐        ┌──────────────────────┐
   │  serve.py ── /metrics                │        │  GitHub Actions      │
   │    counters, histogram, gauges       │        │    every 15 minutes  │
   └───────────────┬──────────────────────┘        │    probe.py          │
                   │ scraped by Prometheus         └───────────┬──────────┘
                   │ (the CI cluster)                          │ Influx line
                   ▼                                           ▼ protocol
            service.json                                    slo.json
        "inside the process"                        "the promise, from outside"
```

**`service.json` is fed by Prometheus scraping `/metrics`.** It is exact about
everything that reached the process and silent about everything that did not.

**`slo.json` is fed by `probe.py`,** which runs on a machine that is not part of
the deployment and asks over the public internet. It measures DNS, TLS, the
platform, the cold start and the answer, because a user does.

### Why the objective is measured from outside

The service runs on Cloud Run with `--max-instances 1` and scales to zero. A
Prometheus scraping it would find, after any quiet period, a process that has
just started with its counters at zero. `rate()` over that is not a traffic
figure, it is a restart detector. Scraping would only work with an instance
pinned up permanently, which costs real money every month for a portfolio
project that is idle most of the day.

So the numbers that back the SLO come from the probe, and that turns out to be
the better answer rather than a consolation. The probe counts the requests the
server could not: the connection it refused, the certificate that expired, the
seven seconds a user waited for a container to boot.

### What the probe still cannot see

Being clear about the next blind spot is the job, not a disclaimer.

- **Real users.** Five requests every fifteen minutes from one datacentre is a
  sample, not traffic. A failure that only affects one browser, one country or
  one question is invisible here.
- **Anything between the probes.** A two-minute outage has a 1-in-7 chance of
  landing entirely between two runs and never being recorded.
- **Why.** The probe says a request took 7.6 seconds. Whether that was a cold
  start, a slow embedding call or the network is a question for `service.json`,
  and answering it is what tracing would be for.

## The five checks

Each exists for a specific failure, and `test_probe.py` serves that failure to
the probe and requires it to notice.

| Check | Fails when |
|---|---|
| `unauthenticated` | the service stops asking strangers for a token |
| `health` | it is unreachable; its duration is the closest thing to a cold-start measurement |
| `search_hit` | a known question stops returning its known document, which is an index that loaded wrong and is answering confidently from the wrong file |
| `search_refused` | a student is not refused, **or is refused in words that describe what was refused** |
| `corpus` | the catalogue names a document the caller may not read |

**Three of the five are about access control, not uptime.** They can all fail
while availability is 100%, which is why they have their own panel and why
`KbAccessControlFailing` is the one page in this repository that fires on a
single failed sample.

### It costs nothing to run

Every question the probe asks is already in `vectors.json`, so no check reaches
the embedding provider and no run spends the API budget. That is a property,
not a coincidence: changing a question here would start billing the project once
every fifteen minutes, quietly, forever.

## Setting it up

The probe prints its measurements with no credentials at all, so this works
before anyone has a Grafana account:

```bash
python observability/probe.py --url https://kb-zv5i45k6sq-uc.a.run.app
```

To send them somewhere that keeps them, three secrets are needed in the GitHub
repository, under Settings, Secrets and variables, Actions:

| Secret | Where it comes from |
|---|---|
| `GRAFANA_PUSH_URL` | Grafana Cloud, your stack, Prometheus, "Send Metrics". Take the remote-write URL and replace the path with `/api/v1/push/influx/write` |
| `GRAFANA_USER` | the numeric instance ID shown on the same page |
| `GRAFANA_TOKEN` | a Cloud Access Policy token with the `metrics:write` scope |

Until all three exist the scheduled job still runs and still prints, and says
`no Grafana credentials set; printed only` rather than failing. A monitoring job
that goes red for a missing credential teaches whoever owns it to ignore a red
monitoring job.

Then import both dashboards: Dashboards, New, Import, upload the file from
`dashboards/`, and pick the Prometheus data source when asked.

## Why Influx line protocol

Prometheus's own `remote_write` is protobuf inside snappy inside HTTP, which
means a client library, which means a dependency, in the one component whose job
is to keep working when other things do not. Grafana Cloud also accepts InfluxDB
line protocol at `/api/v1/push/influx/write` and turns `measurement,tag=v field=1`
into `measurement_field{tag="v"}`. That is one `urllib` call and no dependencies,
and the metric names come out the same.

## Files

| | |
|---|---|
| `probe.py` | the five checks, and the push |
| `test_probe.py` | eleven tests that make the probe fail on purpose |
| `check_dashboards.py` | extracts every panel query so CI can parse it and verify it names a metric something actually emits |
| `dashboards/slo.json` | the objective, from outside |
| `dashboards/service.json` | counters, latency, breaker state, token versions, per-pod load |

The dashboards are plain JSON and are meant to be edited in Grafana and exported
back. CI does not check that they look good; it checks that every query parses
and that no panel asks for a metric that does not exist, which are the two ways
a dashboard silently becomes decoration.
