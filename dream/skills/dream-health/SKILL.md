---
name: dream-health
description: Inspect the operational health of the dream stack, expose Prometheus metrics, and drive the circuit-breaker mode (NORMAL / CONSERVATEUR / SECURISE). Use when the user asks "etat de dream", "sante du palais", "metriques memoire", "dream health", "is dream healthy", "circuit breaker status". Reports RAM, latencies, consensus rate, ledger integrity.
---

# Dream Health

Provide a snapshot of the dream stack and decide the operating mode.

## Instructions for Claude

1. Call `dream__health_check` on the MCP server. It returns:

```json
{
  "uptime_s": 91230,
  "mode": "NORMAL | CONSERVATEUR | SECURISE",
  "ram_peak_mb": 13420,
  "ram_current_mb": 9210,
  "latency_p50_ms": 84,
  "latency_p95_ms": 287,
  "consensus_rate_24h": 0.78,
  "vitality_avg": 0.61,
  "ledger_merkle_ok": true,
  "models_loaded": ["gemma4:e4b", "bge-m3"],
  "circuit_state": "closed | half_open | open"
}
```

2. Cross-check against the circuit breaker thresholds (see below). If the server's `mode` disagrees with the computed mode, override via `dream__set_mode` and log the transition.

3. Render the report in compact form (see Output section). Include any active warnings as a short bullet list with absolute paths to the relevant logs.

4. If `ledger_merkle_ok` is `false`, immediately switch to `SECURISE` mode, halt the next consolidation cycle, and surface the failing leaf id with the recovery commands.

## Circuit breaker thresholds

| Mode | Trigger | Behaviour |
|------|---------|-----------|
| NORMAL | all checks green | PGT 2.0 complet, debate, contrefactuel |
| CONSERVATEUR | `latency_p95 > 500 ms` OR `consensus_rate < 0.7` | fallback retrieval lecture seule, quorum reduit 2/3, contrefactuel desactive |
| SECURISE | `vitality_avg < 0.4` OR `ledger_merkle_ok == false` OR `ram_peak > 15000` | arret contrefactuel, HITL obligatoire, consolidation differee |

The transition is sticky: a mode demotion requires 3 consecutive green probes before promoting back up.

## Sortie utilisateur (FR)

```
Dream health 2026-05-16 14:02
- mode: NORMAL (uptime 1j 1h)
- RAM: 9.2 / 13.4 Go pic (limite 14.5)
- latence: p50 84 ms, p95 287 ms
- consensus 24h: 78% | vitalite moyenne: 0.61
- ledger: ok (root 4a91...)
- modeles charges: gemma4:e4b, bge-m3
```

Si une regle de transition est active, ajouter une ligne `! transition: CONSERVATEUR car latency_p95 > 500ms`.
