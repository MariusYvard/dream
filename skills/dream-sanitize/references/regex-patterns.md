# Regex Patterns Reference

The regex sweep runs in two passes (pre-LLM and post-LLM) with the same pattern set.

## Patterns

| Type | Regex | Placeholder |
|------|-------|-------------|
| Anthropic key | `sk-ant-[A-Za-z0-9_-]{40,}` | `<SECRET:anthropic_key>` |
| OpenAI key | `sk-[A-Za-z0-9]{32,}` | `<SECRET:openai_key>` |
| GitHub PAT | `gh[pousr]_[A-Za-z0-9_]{36,}` | `<SECRET:github_pat>` |
| AWS access key | `AKIA[0-9A-Z]{16}` | `<SECRET:aws_key>` |
| AWS secret | `(?i)aws(.{0,20})?(secret\|private).{0,5}[:=]\s*['\"]?([A-Za-z0-9/+=]{40})` | `<SECRET:aws_secret>` |
| Generic API key | `(?i)(api[_-]?key\|apikey)['\"\s:=]+([A-Za-z0-9_\-]{24,})` | `<SECRET:api_key>` |
| JWT | `eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+` | `<SECRET:jwt>` |
| Bearer token | `(?i)bearer\s+[A-Za-z0-9_\-.]{20,}` | `<SECRET:bearer>` |
| Email | `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b` | `<EMAIL>` |
| Phone FR | `\b0[1-9](?:[\s.-]?\d{2}){4}\b` | `<PHONE>` |
| Phone intl | `\+\d{1,3}[\s.-]?\d{1,4}[\s.-]?\d{2,4}[\s.-]?\d{2,4}[\s.-]?\d{0,4}` | `<PHONE>` |
| IBAN | `\b[A-Z]{2}\d{2}[A-Z0-9]{1,30}\b` | `<IBAN>` |
| Credit card | `\b(?:\d[ -]*?){13,16}\b` | `<CARD>` |
| IPv4 | `\b(?:\d{1,3}\.){3}\d{1,3}\b` | `<IP>` |
| `.env` path | `[\w./-]*\.env(?:\.[\w-]+)?` | `<ENV_PATH>` |
| Private key block | `-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----` | `<SECRET:private_key>` |
| Slack webhook | `https://hooks\.slack\.com/services/[A-Za-z0-9/]+` | `<SECRET:slack_webhook>` |
| Stripe key | `(sk_live\|pk_live\|rk_live)_[A-Za-z0-9]{24,}` | `<SECRET:stripe_key>` |

## Order of operations

1. Apply the most specific patterns first (keys with vendor prefix) to avoid false captures by the generic `api_key` rule.
2. Apply the email and phone patterns last in the first pass, because they have higher false-positive risk.
3. The post-LLM pass runs the same set in the same order. Idempotence is preserved by checking that no placeholder is wrapped twice.

## False positives to whitelist

Common engineering tokens that look like secrets but are not:

- UUIDs: do not redact, they are graph identifiers.
- Git SHAs: do not redact, kept for ledger traceability.
- Semver strings: kept.
