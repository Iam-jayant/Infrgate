<div align="center">
  <h1>infrgate-sdk</h1>
  <p><strong>Python SDK for InfrGate — the intelligent LLM inference gateway.</strong></p>
  
  [![PyPI](https://img.shields.io/pypi/v/infrgate-sdk?color=blue)](https://pypi.org/project/infrgate-sdk/)
  [![Python](https://img.shields.io/pypi/pyversions/infrgate-sdk)](https://pypi.org/project/infrgate-sdk/)
  [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
</div>

<br />

A lightweight, fully-typed Python client for the [InfrGate](https://github.com/Iam-jayant/Infrgate) LLM gateway. Drop-in replacement for the OpenAI SDK — just change the import and `base_url`.

## Installation

```bash
pip install infrgate-sdk
```

## Quick Start

### Chat Completion

```python
from infrgate_sdk import InfrGateClient

client = InfrGateClient(
    base_url="https://infrgate.onrender.com",
    api_key="your-tenant-api-key",
)

response = client.chat.completions.create(
    model="Qwen/Qwen2.5-72B-Instruct",
    messages=[{"role": "user", "content": "Explain quantum computing in one sentence."}],
)

print(response.choices[0].message.content)
# => "Quantum computing uses qubits that can exist in superposition..."

print(f"Tokens used: {response.usage.total_tokens}")
```

### Streaming

```python
stream = client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "Write a haiku about code."}],
    stream=True,
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

Or use `.collect()` for convenience:

```python
stream = client.chat.completions.create(model="...", messages=[...], stream=True)
full_text = stream.collect()
```

### Async

```python
from infrgate_sdk import AsyncInfrGateClient

async with AsyncInfrGateClient(base_url="...", api_key="...") as client:
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Hello!"}],
    )

    # Async streaming
    stream = await client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "Tell me a joke."}],
        stream=True,
    )
    async for chunk in stream:
        print(chunk.choices[0].delta.content or "", end="")
```

### Admin Operations

```python
from infrgate_sdk import InfrGateAdmin

admin = InfrGateAdmin(
    base_url="https://infrgate.onrender.com",
    admin_key="your-admin-key",
)

# Create a tenant
tenant = admin.tenants.create(name="Acme Corp", plan="standard", spend_cap_cents=5000)

# Generate an API key (shown only once!)
key = admin.api_keys.create(tenant_id=tenant.id, name="production")
print(f"Save this key: {key.key}")

# Query usage
summary = admin.usage.summary(tenant_id=tenant.id)
print(f"Total cost: ${summary.total_cost_cents / 100:.2f}")
print(f"Total tokens: {summary.total_tokens:,}")

# List tenants
page = admin.tenants.list(status="active", limit=10)
for t in page.items:
    print(f"  {t.name} ({t.plan}) — ${t.current_spend_cents / 100:.2f} spent")
```

## Migrating from OpenAI SDK

If you're currently using the OpenAI Python SDK, switching to InfrGate takes 2 lines:

```diff
- from openai import OpenAI
- client = OpenAI(api_key="sk-...")
+ from infrgate_sdk import InfrGateClient
+ client = InfrGateClient(base_url="https://infrgate.onrender.com", api_key="your-key")

  response = client.chat.completions.create(
      model="Qwen/Qwen2.5-72B-Instruct",
      messages=[{"role": "user", "content": "Hello!"}],
  )
```

## Features

| Feature | Description |
|:---|:---|
| **OpenAI-compatible** | `.chat.completions.create()` — identical interface |
| **Sync + Async** | Both `InfrGateClient` and `AsyncInfrGateClient` |
| **Streaming** | `for chunk in stream:` — Pythonic SSE iteration |
| **Auto-idempotency** | `Idempotency-Key` auto-generated per request |
| **Retry with backoff** | Automatic retry on 429/5xx with exponential backoff |
| **Typed responses** | Full Pydantic v2 models — IDE autocomplete everywhere |
| **Admin client** | Tenant CRUD, API key management, usage queries |
| **Typed exceptions** | `AuthenticationError`, `RateLimitError`, `ProviderError`, etc. |
| **Minimal deps** | Only `httpx` + `pydantic` |
| **py.typed** | Full mypy / pyright support |

## Error Handling

```python
from infrgate_sdk import InfrGateClient, RateLimitError, AuthenticationError, ProviderError

client = InfrGateClient(base_url="...", api_key="...")

try:
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hi"}],
    )
except AuthenticationError:
    print("Invalid API key")
except RateLimitError as e:
    print(f"Rate limited — retry after {e.retry_after}s")
except ProviderError as e:
    print(f"Provider failed: {e.message}")
```

## Configuration

```python
import httpx
from infrgate_sdk import InfrGateClient

client = InfrGateClient(
    base_url="https://infrgate.onrender.com",
    api_key="your-key",
    timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=10.0),
    max_retries=3,
    default_headers={"X-Custom-Header": "value"},
)
```

## Development

```bash
cd sdk
pip install -e ".[dev]"
pytest -v
```

## License

MIT — see the [LICENSE](../LICENSE) file.
