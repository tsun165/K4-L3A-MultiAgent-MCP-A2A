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
| Coordinator | Case input | Phân phối task cho 4 agent, tổng hợp kết quả (hợp nhất evidence refs, loại trùng), lọc kết quả có confidence cao nhất, trả về format cuối cùng | Gọi agent, gọi Verifier, ghi trace `handoff` |
| Order/item | `get_order`, `get_order_items` qua MCP | Phân loại `canceled_order_paid` hoặc `unavailable_order_paid` dựa trên net_paid từ payment_agent | `SpecialistResult` → Coordinator |
| Payment | Order payments, items, refunds via MCP (`get_order_payments`, `get_order_items`, `get_refund_timeline`) | Classify payment issue (`valid_split_payment`, `payment_mismatch`, `duplicate_charge`, `refund_pending`, `refund_failed`); calculate `financial_resolution` with `Decimal` (2dp); detect duplicates; provide `total_paid`/`total_refunded` to Order agent | `SpecialistResult` → Coordinator; `extra.total_paid_brl` → Order agent for `canceled_order_paid` |
| Shipment | `get_shipment_summary` qua MCP | Xác định `late_delivery_seller` (trễ carrier) hoặc `late_delivery_logistics` (trễ so với estimated) | `SpecialistResult` → Coordinator |
| Policy | Case claims, `policy_version`, policy data via MCP (`get_policy`) | Read policy rules; determine customer entitlement; emit `policy_decided` trace event; map decision to `case_status` and `resolution_actions` | `SpecialistResult` → Coordinator; `extra.decision_code` for cross-check |
| Verifier | Draft output từ Coordinator | Đảm bảo invariants: match `case_id`, tổng refund khớp, trạng thái phù hợp action, pass JSON schema. Sửa lỗi hoặc hạ confidence. | Output cuối cùng an toàn; emit `verification_completed` |

Nêu rõ actor nào được quyền gọi tool nào. Tránh cho mọi agent quyền truy vấn tất cả tool nếu không cần thiết.

## 3. A2A protocol

Điều phối sử dụng `handoff` trace events. Coordinator gọi từng specialist agent (tuần tự hoặc song song).
Mỗi specialist khi bắt đầu xử lý được cấp quyền truy cập `EvidenceGateway`.
Khi trả về, specialist trả `SpecialistResult`.
Coordinator thu thập tất cả `SpecialistResult` và emit `handoff` về coordinator. 
Không có loop giữa các agent, do đó không bị vô hạn (timeout được cấu hình tại MCP HTTPX level 30s).

## 4. Evidence lifecycle

1. Specialist gọi `_fetch_*` qua `EvidenceGateway`.
2. `EvidenceGateway` gọi MCP và dùng JSON schema validate.
3. Nếu thành công, extract `evidence_ref` và emit `tool_result_consumed` ghi vào trace.
4. `evidence_ref` được trả về trong `SpecialistResult` và Coordinator tổng hợp, loại bỏ các duplicate, sau đó giới hạn tối đa 30 refs cho top-level `evidence_refs`.

## 5. Failure policy

| Failure | Retry? | Fallback | Trace event/code |
| --- | --- | --- | --- |
| MCP timeout | Có, httpx tự retry (giới hạn) | Trả `None` / `[]` để agent hạ cấp `insufficient_evidence` | - |
| Not found | Không | `primary_issue = None`, agent bỏ qua lỗi | `tool_result_consumed` không có ref |
| Source conflict | Không | Ưu tiên nguồn MCP thay vì claim | Ghi log data_conflicts |
| Invalid specialist result | Không | Verifier bắt lỗi và downgrade về an toàn | `verification_completed` REPAIRED / DOWNGRADED |

Retry phải có giới hạn (ở httpx) và idempotent. Thiếu dữ liệu thì agent set `confidence = 0.5` hoặc thấp hơn.

## 6. Verification invariants

- **Schema:** Pass qua JSON Schema Validator (`day09-l3a-output-v2`).
- **Money totals:** `recommended_refund_brl == sum(refund_lines.amount_brl)`.
- **Status/Action consistency:** `case_status == "no_action"` => refund phải là 0, không có action. `action_required` => phải có action.
- **Fallbacks:** Nếu vi phạm, sửa (`repaired_output`) bằng cách xoá các mục lỗi, hạ status về `needs_investigation`, refund về 0.0, và confidence về 0.3.

## 7. Reproducibility

- Model/Agent: Hardcoded python rules thay vì prompt LLM, độ lặp lại 100%.
- Concurrency: Tuần tự để đảm bảo logging rõ ràng.
- Dependencies: `mcp`, `httpx`, `jsonschema`.
- Lệnh chạy: `day09 run`
- Hạn chế: Thời gian gọi MCP phụ thuộc network nhưng được timeout 30s.

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
