# Friction Control

Dynamic tool-call rate limiting that adjusts friction per tool based on observed usage, converging toward configured targets. Inspired by proof-of-work difficulty adjustment.

The use case: an MCP server with read-only tools that callers can hit freely, and a smaller set of destructive ones (delete, update) you'd rather not have hammered. Static rate limits force a choice between too lax (clients can delete everything) and too strict (clients hit limits on legitimate bursts). Friction control lets you specify a *target rate* (say, "delete calls should be about 3% of total tool usage") and the controller continuously adjusts cost to converge on it.

## Setup

```python
from mcp_authflow_resource import (
    ControllerConfig,
    FrictionRegistry,
    ToolFrictionConfig,
    ToolGroupConfig,
    friction_controlled,
    init_friction,
    record_tool_call,
)

init_friction(FrictionRegistry(
    default_config=ControllerConfig(
        window_size=100,        # Sliding window of last 100 calls
        time_decay_rate=0.001,  # ~11.5 min half-life for idle decay
        warmup_calls=20,        # No adjustment during first 20 calls
    ),
    tool_configs={
        "delete_task": ToolFrictionConfig(target_rate=0.03),  # 3% of calls
        "update_task": ToolFrictionConfig(target_rate=0.10),  # 10% of calls
    },
    tool_groups={
        "mutations": ToolGroupConfig(
            tools=["delete_task", "update_task"],
            aggregate_target=0.20,  # Combined 20% of all calls
        ),
    },
))
```

Call `init_friction` once at server startup. The registry is process-global and shared across every tool decorated with `friction_controlled` or `record_tool_call`.

## Decorators

```python
# Mutation tools: checks friction before execution, blocks if too high
@app.tool()
@friction_controlled()
async def delete_task(task_id: str) -> str:
    ...

# Read tools: records call without friction checks (for rate denominator)
@app.tool()
@record_tool_call()
async def get_tasks(status: str) -> str:
    ...
```

Read tools need [`record_tool_call`][mcp_authflow_resource.record_tool_call] so they count toward the denominator when computing rates. Without it, a "100% of calls are deletes" reading would be technically true but meaningless.

## How it works

The controller tracks tool calls in a sliding window and computes an exponential moving average (EMA) of each tool's usage rate. When a tool's EMA exceeds its target, friction increases, raising the cost and eventually blocking calls. When usage drops, friction decreases (2× faster than it rises, to avoid trapping clients).

| Friction Level | Effect |
|---|---|
| `0.00 – 0.59` | NONE / LOW / MEDIUM: tool executes normally. |
| `0.60 – 0.94` | HIGH: `justification_required=True` returned in the [`FrictionResult`][mcp_authflow_resource.friction.FrictionResult]. |
| `0.95 – 1.00` | BLOCKED: call denied, error returned. |

Above the saturation threshold (default 0.9, sustained), the controller emits a `friction_saturation` event and triggers a small automatic relief to avoid a permanent block under legitimate load spikes.

## Key parameters

| Parameter | Default | Description |
|---|---|---|
| `window_size` | 100 | Number of recent calls to track. |
| `time_decay_rate` | 0.001 | Exponential friction decay (~11.5 min half-life). |
| `warmup_calls` | 20 | Calls before friction adjustment begins. |
| `target_rate` | 0.05 | Desired tool usage fraction (0.0–1.0). |
| `justification_threshold` | 0.6 | Friction level requiring justification. |
| `hard_block_threshold` | 0.95 | Friction level that blocks the call. |
| `saturation_threshold` | 0.9 | Triggers automatic relief if sustained. |

### Security-relevant defaults

Two parameters ship with defaults that leave a protection *disabled* until you
opt in. Set them deliberately — discovering them through the API reference and
tuning them without this context can silently remove a safeguard.

| Parameter | Default | Safety note |
|---|---:|---|
| `default_budget` | `inf` | Cost enforcement is disabled. Tool-use spending is unbounded until you set a finite per-client budget; use one when tool calls carry real cost or abuse risk. |
| `saturation_window` | `0` | Automatic saturation relief is disabled, so sustained saturation can remain near block until normal decay brings it down. Set it to the number of consecutive saturated calls that should trigger relief, and pair it with `saturation_threshold` so legitimate sustained load can recover. |

The remaining `ControllerConfig` fields (`ema_alpha`, `adjustment_rate`,
`asymmetric_decay`, `dead_zone`, `saturation_relief_rate`) are tuning knobs for
the adjustment loop rather than safety switches; see the
[`ControllerConfig`][mcp_authflow_resource.ControllerConfig] reference for their
defaults and semantics.

See [`ControllerConfig`][mcp_authflow_resource.ControllerConfig] and [`ToolFrictionConfig`][mcp_authflow_resource.ToolFrictionConfig] for the full set, including per-tool overrides and group aggregation parameters.

## Observability

Friction events are emitted as structured JSON via Python's `logging` module:

```python
# Logger names
"mcp_authflow_resource.friction"           # check/record events (INFO)
"mcp_authflow_resource.friction.block"     # blocked calls (WARNING)
"mcp_authflow_resource.friction.registry"  # client lifecycle (DEBUG)
```

Event types: `friction_check`, `friction_block`, `friction_justification`, `friction_saturation`.

Fields: `event_type`, `client_id`, `tool_name`, `friction_level`, `ema_rate`, `target_rate`, `cost`, `allowed`.

Pipe these into your structured log stack (Loki, Datadog, etc.) and dashboard `ema_rate` against `target_rate` per tool to see whether the loop is converging.

## When to use this

Friction control is most useful when:

- You have a high read-to-write ratio and want soft-but-firm guarantees on the write rate.
- You can describe "normal" usage as a percentage rather than a hard rate (5% of calls is reasonable, 50 calls/minute is not).
- You want misbehaving clients to feel cost gradually, not get cut off instantly.

It is **not** a substitute for:

- Authentication or authorization. Friction kicks in *after* the token verifier accepts the request.
- Static rate limits on truly destructive operations. If you want "no more than 1 `delete_database` per hour, ever", use a hard limit.
