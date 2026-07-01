# ADR-0007 — v1 security envelope: default-safe with opt-out

Status: accepted
Date: 2026-07-01

## Context

v1 runs `docker compose` against the host daemon (ADR-0002), so untrusted
containers are siblings on host networking and can, by default, reach the
internet, the host's private networks, and the cloud metadata endpoint (§20.1,
§25 恶意仓库 risk). Concern raised: over-tight restrictions could break the core
goal (projects need to download packages / make outbound connections), and for a
single-operator CLI (ADR-0001) it is reasonable to let the operator bear risk.

Key clarification: the proposed egress block targets only **private ranges**
(`10/8, 172.16/12, 192.168/16`) and **link-local/metadata** (`169.254/16`). Public
egress — pypi, npm, Maven Central, Go proxy, crates, public APIs, CDNs — is always
allowed. Package downloads and normal outbound calls are unaffected. Multi-service
repos talk over the shared compose network, which is not filtered.

## Decision

**Default-safe with explicit opt-out.** For each job:

1. **Egress**: public internet allowed; DROP traffic to `10/8, 172.16/12,
   192.168/16, 169.254/16` (incl. `169.254.169.254`) from the job's compose
   network, via host firewall rules. Intra-compose traffic allowed.
2. **Secrets: none.** No host env passthrough. `.env.example` vars get dummy
   generated values (§20.2). The compose compiler (ADR-0003) is the sole writer of
   env — enforced in one place.
3. **Log redaction on by default** (§20.2 patterns) before artifacts are stored.
4. **Resource limits mandatory** — CPU/memory/pids + wall-clock timeout per job,
   emitted by the compiler on every service. Also serves the pipeline: a hung
   start must time out to become a §9.2 failure signal for the Repair Loop.
5. **Command policy: flag, don't block** (§20.3) in v1 — high-risk patterns
   recorded as evidence/warnings, not refused; blast radius already contained by
   sandbox + egress.

**Opt-out flags:** `--allow-private-egress`, `--allow-metadata`, `--no-isolation`.
The dangerous mode is a deliberate operator choice, never a silent default. The
**metadata block is strongly recommended to stay on** even when relaxing others —
zero cost to legitimate runs, catastrophic/irreversible downside (leaked cloud
credentials on a cloud VM).

## Consequences

- Package downloads and public connectivity work out of the box; the block only
  costs LAN-scanning and metadata theft — neither legitimate.
- Operator can consciously accept more risk per-repo via flags (honors
  user-bears-risk for a single-operator tool).
- Residual risk: plain bridge + firewall is weaker than gVisor/Firecracker; a
  container-escape 0-day reaches the host. Acceptable for single trusted operator;
  not for multi-tenant. Stronger isolation (§8.3) and egress proxy/allowlist +
  command blocking remain mid-term (§20).
