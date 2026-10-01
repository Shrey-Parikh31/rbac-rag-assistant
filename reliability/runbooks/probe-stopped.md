# KbProbeStopped: the outside checks have gone blind

No external probe has reported for six hours. **This says nothing about whether
the service is up.** It says the only thing that could tell you from outside has
stopped talking, and that every other probe-based alert is now silent for the
wrong reason.

The two alerts this covers for, `KbAccessControlFailing` and
`KbUnreachableFromOutside`, both ask a question about samples that exist. With
no samples their expressions return nothing at all, which Prometheus treats as
"no result" and not as "bad". So they go quiet exactly when they matter most.
That is why this rule exists and why it fires on absence rather than on a value.

## First five minutes

1. **Is the service actually up?** Answer this before anything else, by hand,
   because the thing that would normally tell you is the thing that is broken.

   ```
   curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" "$KB_URL/health"
   ```

2. **Did the workflow run?** `gh run list --workflow=probe.yml --limit 5`.
   Three outcomes, three different problems:

   | What you see | What it means |
   |---|---|
   | runs, all green | the probe ran and the push failed; go to step 3 |
   | runs, failing | read the log, the probe prints which check and why |
   | no runs at all | the schedule stopped; go to step 4 |

3. **The push failed.** The probe prints `push FAILED: HTTP ...` to stderr and
   exits 2, deliberately loudly, so the monitor going down never looks like the
   thing it monitors going down. Almost always the Grafana Cloud access policy
   token expired or was rotated. Check `GRAFANA_PUSH_URL`, `GRAFANA_USER` and
   `GRAFANA_TOKEN` in the repository secrets.

   If it instead prints `no Grafana credentials set; printed only`, the secrets
   were never added. The probe is working and has nowhere to put its numbers.

4. **The schedule stopped.** In rough order of likelihood:

   - **GitHub disabled it for inactivity.** Scheduled workflows are paused on a
     public repository with no commits for 60 days. The banner is on the Actions
     tab, and one click re-enables it. This is the most common cause by a wide
     margin on a portfolio project.
   - The workflow file was renamed, moved or has a YAML error, in which case
     the schedule silently does not exist.
   - GitHub Actions is degraded. Check their status page before assuming it
     is you.

## Do not tighten this window

Six hours looks generous for a probe whose cron says fifteen minutes. The cron
is not the delivery rate: measured, it is about one run every three hours. Six
hours is two missed probes. Tightening it to match the cron brings back the
original bug, where the window was shorter than the gap between samples and the
alert could never fire at all.
