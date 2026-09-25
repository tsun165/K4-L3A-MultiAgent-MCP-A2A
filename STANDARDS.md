# STANDARDS — Chuẩn chung cho code L3A

> **Nguồn chuẩn duy nhất** cho mọi người viết code (Antigravity, Claude Code) và người review (Sơn, Đạt).
> Khi `PLAN.md`, prompt hay code khác với file này thì **file này đúng**. Muốn đổi chuẩn: sửa file này trong một PR riêng, Khoa duyệt.

---

## 1. Vai trò

| Ai | Vai trò | Được sửa |
| --- | --- | --- |
| **Khoa** (`Dokhacgiakhoa`) | Điều phối, giữ `.env`, chạy full run, nộp bài | Mọi thứ |
| **Claude Code** (AI, dưới tài khoản Khoa) | Viết **toàn bộ** code: interface, coordinator, verifier, calibration, tool registry, 4 specialist agent, mã chuẩn, test | Xem §2 |
| **Antigravity** (AI, dưới tài khoản Khoa) | Đã dừng ở Task 1 (interface + skeleton, PR #4). Từ đây Claude Code tiếp quản toàn bộ, kể cả phần core. | — |
| **Sơn** (`tsun165`) | Review + test/backtest: order, shipment, trace/workflow | Chỉ file test, comment PR |
| **Đạt** (`Liber72`) | Review + test/backtest: payment, policy, verifier, tiền | Chỉ file test, comment PR |

Sơn và Đạt **không** push code logic. Branch `hoangthaidat` không được merge. Nếu có ý tưởng hay trong đó, chuyển thành comment trên PR.

## 2. Quyền sở hữu file (tránh conflict)

| File | Owner | Ghi chú |
| --- | --- | --- |
| `agents/base.py`, `agents/__init__.py` | Claude Code | Interface ở §5: **đóng băng**, đổi phải báo trước |
| `workflow.py`, `agents/verifier.py`, `calibration.py` | Claude Code | |
| `ARCHITECTURE.md` | Claude Code | Cập nhật sau khi specialist xong |
| `agents/tools.py` | Claude Code | **Tool registry**, xem §4 |
| `agents/order_agent.py`, `agents/shipment_agent.py` | Claude Code | Reviewer: Sơn |
| `agents/payment_agent.py`, `agents/policy_agent.py` | Claude Code | Reviewer: Đạt |
| `agents/vocab.py` | Claude Code | Mã chuẩn ở §7 |
| `notes/mcp-tools.md` | Người chạy `day09 mcp-tools` | Không chứa key |
| `tests/test_<module>.py` | Người viết module; Sơn/Đạt được thêm test | |
| `cli.py`, `mcp_gateway.py`, `trace.py`, `contracts.py`, `submission.py`, `contracts/` | **Không sửa** | Starter kit của BTC |

Một PR chỉ sửa file của một owner. Cần sửa file của owner khác thì comment trên PR của owner đó.

## 3. Git & PR

- Branch: `feat/<phạm-vi>` (vd. `feat/core-khoa`, `feat/specialists-order-shipment`). Không commit thẳng lên `main`, trừ tài liệu.
- PR nhỏ, một mục đích. Tiêu đề theo Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`).
- Mô tả PR phải có: phạm vi, cách test, và câu *"Tên tool đã đối chiếu `notes/mcp-tools.md`: có/không"*.
- Merge khi: `pytest -q` pass, `ruff check src tests` pass, có ≥ 1 approve (Sơn hoặc Đạt theo §1), Khoa bấm merge.
- Hai agent AI làm chung một thư mục repo thì **không được `git checkout` đổi branch** của nhau. Dùng `git worktree` nếu cần làm branch khác.

## 4. Tool MCP: chỉ dùng tên thật

1. Tên tool **chỉ** lấy từ output `day09 mcp-tools`, ghi vào `notes/mcp-tools.md` theo dạng: tên tool, tham số, `domain` trả về, các field chính trong `data`.
2. Mọi tên tool trong code phải là **hằng số trong `agents/tools.py`**. Không viết chuỗi `"get_..."` rải rác trong agent.

   ```python
   # agents/tools.py — mọi tên ở đây phải có trong notes/mcp-tools.md
   ORDER_TOOLS: frozenset[str] = frozenset({...})
   SHIPMENT_TOOLS: frozenset[str] = frozenset({...})
   PAYMENT_TOOLS: frozenset[str] = frozenset({...})
   POLICY_TOOLS: frozenset[str] = frozenset({...})
   ```

3. Mỗi specialist đặt `allowed_tools = <X>_TOOLS`. Coordinator đăng ký `store.register_actor_tools(s.name, s.allowed_tools)` cho **mọi** actor. Actor chưa đăng ký gọi tool ⇒ `PermissionError` (fail-closed).
4. Có một test đối chiếu mọi tên trong `agents/tools.py` với `notes/mcp-tools.md`. Tên không có trong notes ⇒ test fail.
5. Tên tool hiện có trong PR #4/#5 và trên branch `hoangthaidat` **chưa được xác nhận**. Không dùng cho tới khi đối chiếu xong.

## 5. Interface (đóng băng)

Theo `agents/base.py` trên PR #4:

```python
EvidenceRecord(evidence_ref, result_hash, domain, tool_name, data, warnings)   # frozen

EvidenceStore(case_id, gateway, trace)       # MỘT instance mới cho mỗi case
  .register_actor_tools(actor, tools)
  await .fetch(actor, tool_name, **arguments) -> EvidenceRecord  # tự emit tool_result_consumed
  .owns(ref) -> bool ; .records(domain=None) ; .refs() -> set[str]

SpecialistResult(actor, findings, candidate_issues: list[tuple[str, float]],
                 entities, cause_codes, responsible_parties, refund_lines,
                 evidence_refs, errors)

Specialist: name, allowed_tools, async run(case, store, trace) -> SpecialistResult
AgentMessage(case_id, sender, recipient, task, payload, evidence_refs, message_id)
send(message, trace)                          # emit handoff
```

Quy định cho specialist:

- **Không raise** ra ngoài `run()`: lỗi thì ghi `errors`, trả kết quả của những gì đã có.
- `evidence_refs` chỉ gồm ref **hỗ trợ trực tiếp** finding (lấy từ `record.evidence_ref`), không dump mọi ref đã gọi.
- `entities` chỉ chứa ID **xuất hiện trong `data` của evidence**. Không lấy từ `customer_request`.
- `candidate_issues` score trong [0, 1]; mỗi agent chỉ đề xuất issue thuộc domain của mình (§6).

## 6. Input & quyết định nghiệp vụ

Input thật (`inputs/L3A_CASE_XXX.json`):

```json
{ "case_id": "...", "opened_at": "...", "policy_version": "EC_POLICY_V1",
  "customer_request": { "language": "vi", "message": "...", "claimed_order_id": "...",
    "claims": [ {"claim_id": "...", "topic": "<primary_issue>"}, {"claim_id": "...", "topic": "requested_full_refund"} ] } }
```

- 100/100 case có một claim `requested_full_refund` và một claim có `topic` là một trong 10 `primary_issue`, mỗi loại 10 case.
- **`topic` là lời khách nói, không phải đáp án.** Dùng nó để biết cần kiểm gì, nhưng kết luận phải dựa trên evidence. Evidence không ủng hộ ⇒ `unsupported_claim` hoặc issue khác đúng với dữ liệu.
- `claimed_order_id` cũng phải xác minh qua MCP. Không tìm thấy ⇒ không đưa vào `affected_entities`.

Issue theo agent đề xuất:

| Agent | `primary_issue` được đề xuất |
| --- | --- |
| `order-agent` | `canceled_order_paid`, `unavailable_order_paid` |
| `shipment-agent` | `late_delivery_seller`, `late_delivery_logistics` |
| `payment-agent` | `valid_split_payment`, `payment_mismatch`, `duplicate_charge`, `refund_pending`, `refund_failed` |
| coordinator | `unsupported_claim` (claim có topic nhưng evidence phủ định), `insufficient_evidence` (thiếu evidence bắt buộc / tool lỗi) |

`case_status`:

| Tình huống | `case_status` |
| --- | --- |
| Có sai phạm cần hoàn tiền / xử lý | `action_required` |
| `valid_split_payment`, `unsupported_claim` | `no_action` |
| `insufficient_evidence`, `refund_pending` còn trong hạn policy | `needs_investigation` |

Bảng trên là mặc định; policy-agent được ghi đè theo policy MCP (`EC_POLICY_V1`), và phải ghi `decision_code` giải thích.

## 7. Từ vựng mã chuẩn (`agents/vocab.py`)

Mọi mã là UPPER_SNAKE_CASE, định nghĩa một lần trong `agents/vocab.py` và import ở nơi dùng. Không tự đặt mã mới trong agent; cần mã mới thì thêm vào `vocab.py`.

| Loại | Mã |
| --- | --- |
| `cause_code` | `ORDER_CANCELED_AFTER_PAYMENT`, `ORDER_UNAVAILABLE_AFTER_PAYMENT`, `SELLER_LATE_HANDOVER`, `CARRIER_LATE_DELIVERY`, `SPLIT_PAYMENT_VALID`, `PAYMENT_AMOUNT_MISMATCH`, `DUPLICATE_PAYMENT_CAPTURED`, `REFUND_NOT_COMPLETED`, `REFUND_PROCESSING_FAILED`, `CLAIM_NOT_SUPPORTED_BY_EVIDENCE`, `EVIDENCE_UNAVAILABLE` |
| `resolution_actions` | `REFUND_FULL`, `REFUND_PARTIAL`, `REFUND_DUPLICATE_CHARGE`, `RETRY_REFUND`, `MONITOR_REFUND`, `ESCALATE_TO_SELLER`, `ESCALATE_TO_LOGISTICS`, `EXPLAIN_SPLIT_PAYMENT`, `REJECT_CLAIM`, `REQUEST_MANUAL_REVIEW` |
| refund `reason_code` | `CANCELED_ORDER_REFUND`, `UNAVAILABLE_ORDER_REFUND`, `PAYMENT_DIFFERENCE_REFUND`, `DUPLICATE_CHARGE_REFUND`, `FAILED_REFUND_REISSUE`, `LATE_DELIVERY_COMPENSATION` |
| `data_conflicts.resolution_code` | `PREFER_MCP_SOURCE`, `PREFER_LATEST_RECORD`, `UNRESOLVED` |
| `policy_decided.decision_code` | `POLICY_REFUND_ELIGIBLE`, `POLICY_REFUND_NOT_ELIGIBLE`, `POLICY_NEEDS_REVIEW` |
| `verification_completed.decision_code` | `PASS`, `REPAIRED`, `DOWNGRADED` |

`LATE_DELIVERY_COMPENSATION` chỉ dùng khi policy MCP quy định có bồi thường; nếu không, late delivery có refund = 0.

## 8. Tiền

- Tính bằng `Decimal`, `quantize(Decimal("0.01"), ROUND_HALF_UP)`. Chỉ đổi sang `float` khi ghi output.
- `recommended_refund_brl == sum(refund_lines[].amount_brl)`, khớp chính xác tới cent.
- Refund ≤ số tiền đã thanh toán thực tế (theo evidence payment) trừ số đã hoàn.
- `case_status == "no_action"` ⇒ refund = 0, `refund_lines = []`, không có action `REFUND_*`/`RETRY_REFUND`.
- `entity_id` của refund line là ID có trong `affected_entities` (order_id / payment reference), hoặc `null`.

## 9. Trace

Thứ tự bắt buộc mỗi case (CLI đã emit dòng đầu và dòng cuối):

```
case_received → task_assigned(coordinator→each specialist) → tool_result_consumed* →
handoff(specialist→coordinator)* → task_assigned(coordinator→policy-agent) → handoff(policy-agent→coordinator) →
policy_decided → handoff(coordinator→verifier) → verification_completed → case_finalized
```

- Tên actor cố định: `coordinator`, `order-agent`, `shipment-agent`, `payment-agent`, `policy-agent`, `verifier`.
- Mọi ref trong output phải đã xuất hiện trong một event `tool_result_consumed` **của cùng case**.
- Không ghi prompt, lý luận, message khách hay dữ liệu cá nhân vào trace. Chỉ ghi mã và số đếm trong `attributes`.

## 10. Confidence

Dùng `calibration.calculate_confidence(...)`; **không** lấy thẳng score của candidate làm confidence.

| Tình huống | Confidence |
| --- | --- |
| Tín hiệu rõ, đủ evidence bắt buộc, không conflict | 0.85–0.95 |
| Có `data_conflicts` hoặc 2 candidate sát điểm | ≈ 0.60 |
| Verifier phải repair | trừ 0.10 |
| `insufficient_evidence` | 0.25 |
| Verifier downgrade | 0.20 |

`claim_assessments[].evidence_refs` phải chứa ref thật hỗ trợ verdict, không để rỗng khi verdict là `supported`/`unsupported`.

## 11. Cấm tuyệt đối (hard gate = 0 điểm)

- Tự tạo, ghép, sửa, đoán `evidence_ref`; dùng ref của case khác; cache evidence giữa các case.
- Hard-code tên tool chưa có trong `notes/mcp-tools.md`.
- Commit `.env`, key, `inputs/`, `l3a-inputs-*/`, `case-set.json`, `outputs/`, `traces/`, `dist/`.
- Chạy `day09 run` full 100 case hoặc nộp bài: **chỉ Khoa**. AI agent chỉ thử 1–3 case, báo trước khi chạy.
- In hoặc log giá trị API key.

## 12. Checklist review (Sơn / Đạt dùng khi approve)

- [ ] Chỉ sửa file thuộc owner của PR (§2), không đụng starter kit.
- [ ] Tên tool nằm trong `agents/tools.py` và có trong `notes/mcp-tools.md`.
- [ ] Không có chuỗi `ev_` tự ghép trong `src/`.
- [ ] Rule dựa trên field thật của evidence, không dựa vào `customer_request`.
- [ ] Mã dùng từ `agents/vocab.py`; tiền theo §8.
- [ ] Specialist không raise; `evidence_refs` chỉ gồm ref hỗ trợ finding.
- [ ] `pytest -q` và `ruff check src tests` pass trên env sạch (`pip install -e ".[dev]"`).
- [ ] Có test cho rule mới (fixture offline, không gọi MCP).
