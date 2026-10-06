# Milestone 4 closure checklist — v0.4.0

**Deliveries 4.1–4.3 technically accepted by the user. Closure, integration and
annotated `v0.4.0` publication are authorized. No public deployment is authorized.**

## Completed review gates

- [x] User accepted 4.3 after reviewing its [report](milestone-4-3-portfolio.md),
  runbooks, synthetic tours, exports and code/CI evidence.
- [x] Approved candidate `e2cfe4a2877442be9950d069f838199b1eff25ad` passed
  [CI 37393123462](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37393123462):
  PostGIS migrations, OSRM/VROOM smoke and both portfolio tours included.
- [x] Local closure review matched 65 manifest files, all 64 hashes and 18 decoded,
  readable captures. External evidence remains unchanged at
  `C:\Users\erosi\Desktop\routeops-visual-review-v0.4.0`.
  Saved-image inspection is distinct from a new browser test or remote visual review.
- [x] Preserve 4.2 residual advisories, local single-user scope, workload caps
  and benchmarks tied to their original baseline before runtime mitigations.
- [x] Review paired backup/restore and scoped reset safeguards. Windows/Docker
  and Linux CI were executed; user WSL instructions were only reviewed.
  SQL/private-file restoration is not cross-resource atomic.
- [x] Review source license, dependency notices and OSM attribution; metadata
  inventory does not certify redistribution compliance.
- [x] Record blocked cleanup exactly; the 4.1 container/image/OSM rejection is
  not retried and does not condition authorized technical acceptance.

## Publication verification procedure

These are operation gates, not advance claims of remote publication. GitHub CI
and the final closure report record their results on the final documentary commit.

1. Review/stage only closure documentation, check the complete staged diff and
   whitespace, and use Eros Moreno / `erosignacio.m@gmail.com` as author.
2. Push the 4.3 branch and verify successful automatic quality CI on that commit.
   Do not rerun local suites or benchmarks for documentation-only changes.
3. Fetch/review remote main (expected `260faf4a0a0bd4e16578c899fd9825315f66e179`),
   preserve legitimate advances and integrate by fast-forward, or stop for
   divergence review. Never force push. Verify main's push and automatic CI.
4. Confirm absence of `v0.4.0`, create its annotated tag on the final integrated
   commit, publish, and verify both remote tag object and peeled target.
   Local/remote main and the tag's commit must agree.
5. Preserve every prior tag object and target. Retire only the local and remote
   M4.1, M4.2 and M4.3 branches whose tips are proven ancestors of main.
6. Leave main clean/synchronized, check existing service/API/UI availability
   without unnecessary recreation, and preserve data, originals, volumes and
   evidence. Reproduction uses the release tag rather than retired branches.

Authentication, multiuser authorization, public hosting, capacity expansion and
a custom solver are outside this closure. VROOM stays behind `SolverGateway`.
