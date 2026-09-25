# L3A Architecture Record

Team phải cập nhật tài liệu này cùng source. Mục tiêu là mô tả quyết định có thể kiểm chứng, không ghi prompt bí mật hoặc chain-of-thought.

## 1. System overview

Vẽ hoặc mô tả luồng từ `inputs/<case_id>.json` đến MCP calls, specialist agents, verifier, output và trace.

```text
Input → Coordinator → Specialists → Verifier → Output
                         │              │
                         └── MCP ───────┴── Trace
```

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Output/handoff |
| --- | --- | --- | --- |
| Coordinator | TODO | TODO | TODO |
| Order/item | TODO | TODO | TODO |
| Payment | Order payments, items, refunds via MCP (`get_order_payments`, `get_order_items`, `get_order_refunds`) | Classify payment issue (`valid_split_payment`, `payment_mismatch`, `duplicate_charge`, `refund_pending`, `refund_failed`); calculate `financial_resolution` with `Decimal` (2dp); detect duplicates; provide `total_paid`/`total_refunded` to Order agent | `SpecialistResult` → Coordinator; `extra.total_paid_brl` → Order agent for `canceled_order_paid` |
| Shipment | TODO | TODO | TODO |
| Policy | Case claims, `policy_version`, policy data via MCP (`get_policy`) | Read policy rules; determine customer entitlement; emit `policy_decided` trace event; map decision to `case_status` and `resolution_actions` | `SpecialistResult` → Coordinator; `extra.decision_code` for cross-check |
| Verifier | TODO | TODO | TODO |

Nêu rõ actor nào được quyền gọi tool nào. Tránh cho mọi agent quyền truy vấn tất cả tool nếu không cần thiết.

## 3. A2A protocol

Mô tả message envelope, correlation theo `case_id`, điều kiện handoff, timeout và cách tránh vòng lặp. Chỉ trace sự kiện/decision code quan sát được; không trace nội dung suy luận riêng.

## 4. Evidence lifecycle

Mô tả cách validate MCP response, lưu `evidence_ref`, map evidence vào claim/output và emit `tool_result_consumed`. Evidence không được tái sử dụng giữa các case.

## 5. Failure policy

| Failure | Retry? | Fallback | Trace event/code |
| --- | --- | --- | --- |
| MCP timeout | TODO | TODO | TODO |
| Not found | TODO | TODO | TODO |
| Source conflict | TODO | TODO | TODO |
| Invalid specialist result | TODO | TODO | TODO |

Retry phải có giới hạn và idempotent. Không chuyển missing evidence thành dữ liệu phỏng đoán.

## 6. Verification invariants

Liệt kê kiểm tra trước finalize: schema, entity scope, evidence ownership, claim linkage, money totals, responsibility/action consistency và confidence bounds.

## 7. Reproducibility

Ghi model/config, dependency pinning, concurrency limit, random seed (nếu có), lệnh chạy và các giới hạn tài nguyên. Không ghi API key.

---

## A. Payment Agent — Design Record

**File:** `src/student_agent/agents/payment_agent.py`

### A.1 Tools used

| Tool | Purpose |
| --- | --- |
| `get_order_payments` | Fetch all payment records for the order |
| `get_order_items` | Fetch item prices and freight to compute expected total |
| `get_order_refunds` | Fetch refund status/amount |

### A.2 Classification logic (priority order)

1. **`refund_failed`** — Any refund with `refund_status == "failed"` → `action_required`, actions: `retry_refund`, `escalate_refund_failure`
2. **`refund_pending`** — Any refund with `refund_status == "pending"` → `needs_investigation`, action: `escalate_pending_refund`
3. **`duplicate_charge`** — Same `(payment_type, payment_value, installments)` appears twice → `action_required`, action: `refund_duplicate_charge`
4. **`payment_mismatch`** — `total_paid > expected_total` (overpayment) → `action_required`, action: `refund_overpayment`; underpayment → `needs_investigation`
5. **`valid_split_payment`** — Multiple payments summing to expected total → `no_action`

### A.3 Financial resolution

- All amounts use `Decimal` with `ROUND_HALF_UP` to 2 decimal places.
- `expected_total = sum(price) + sum(freight_value)` for all items.
- Invariant: `recommended_refund_brl == sum(refund_lines.amount_brl)`.
- Currency is always `BRL`.

### A.4 Inter-agent data

`extra` dict provides `total_paid_brl`, `total_refunded_brl`, `expected_total_brl` for the Order agent's `canceled_order_paid` rule.

---

## B. Policy Agent — Design Record

**File:** `src/student_agent/agents/policy_agent.py`

### B.1 Tools used

| Tool | Purpose |
| --- | --- |
| `get_policy` | Fetch e-commerce policy rules for the case's policy_version |

### B.2 Decision flow

1. Fetch policy from MCP with `policy_version`.
2. Extract primary topic from the first claim.
3. If policy data contains explicit rules matching the topic, use the server-side decision.
4. Otherwise, use default `topic → decision_code` mapping.
5. Emit `policy_decided` trace event with `decision_code`, `policy_version`, and `primary_topic`.

### B.3 Decision codes → case_status mapping

| Decision Code | Case Status |
| --- | --- |
| `NO_ACTION_NEEDED` | `no_action` |
| `MONITOR_REFUND`, `NEEDS_REVIEW`, `EVALUATE_REFUND_ELIGIBILITY` | `needs_investigation` |
| All others (`FULL_REFUND_ENTITLED`, `REFUND_OVERPAYMENT`, etc.) | `action_required` |

### B.4 Trace events

- `tool_result_consumed` when policy evidence is received.
- `policy_decided` with decision code and attributes.
