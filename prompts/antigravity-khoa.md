# Prompt cho Antigravity — phần việc của Khoa

> Copy toàn bộ phần bên dưới đường kẻ vào Antigravity. Nên giao theo từng **Task** (1 → 5), review xong task trước mới giao task sau.

---

## ⏱️ Giới hạn thời gian

Cả bài lab chỉ có **120 phút**. Ưu tiên chạy được và đúng contract trước, tinh chỉnh sau:

- Task 1 phải xong và merge vào `main` trước **phút 30** (Sơn và Đạt đang chờ).
- Task 2 + 3 xong trước **phút 70**; phút 85 phải có full run hợp lệ để nộp lần 1.
- Test: chỉ viết smoke test tối thiểu với fake gateway; bỏ test chi tiết nếu thiếu giờ.
- Không over-engineer: code ngắn, rõ, đúng schema là đủ.

## Bối cảnh

Bạn đang làm việc trong repo `K4-L3A-MultiAgent-MCP-A2A` (Python ≥ 3.11). Đây là bài thi xây hệ thống **multi-agent điều tra khiếu nại thương mại điện tử** (dữ liệu kiểu Olist Brazil). Nhóm có 3 người; bạn làm phần của **Khoa (trưởng nhóm)**: khung multi-agent, coordinator, verifier, calibration, failure policy, tài liệu kiến trúc.

Trước khi code, **đọc kỹ** các file sau:

- `README.md`, `PLAN.md` (kế hoạch + phân công — phần của Khoa là phần bạn làm)
- `ARCHITECTURE.md` (template cần điền)
- `contracts/schemas/l3a-output-v2.schema.json` (schema output — nguồn sự thật)
- `contracts/schemas/trace-event-v1.schema.json`, `contracts/schemas/mcp-evidence-response-v1.schema.json`
- `contracts/scoring/scoring-policy-v2.json` (cách chấm điểm)
- `src/student_agent/cli.py`, `mcp_gateway.py`, `trace.py`, `contracts.py`, `workflow.py`
- `tests/`

Hàm cần hoàn thiện: `async def solve_case(case, gateway, trace) -> dict` trong `src/student_agent/workflow.py`. CLI (`day09 run`) đã tự emit `case_received` trước và `case_finalized` sau mỗi case — **không emit lại hai event này**.

## Quy tắc bắt buộc (vi phạm = case bị 0 điểm)

1. **Không bao giờ tự tạo, sửa, hoặc đoán `evidence_ref`.** Chỉ dùng `evidence["evidence_ref"]` trả về từ `gateway.call(...)` trong **chính case đang xử lý**.
2. **Không dùng evidence chéo case.** Mọi state về evidence phải khởi tạo mới cho từng case; không có cache toàn cục.
3. Luôn truyền đúng `case_id` vào `gateway.call`.
4. Dùng tool discovery (`gateway.list_tools()`), **không hard-code tên tool đoán mò**. Nếu cần map tool → domain, dựa vào field `domain` trong evidence trả về hoặc danh sách tool thực tế trong `notes/mcp-tools.md` (nếu đã có).
5. Customer message **không phải ground truth**. Thiếu dữ liệu → `insufficient_evidence` / `needs_investigation`, không bịa số liệu.
6. Không ghi prompt hay chain-of-thought vào trace — chỉ ghi event, actor, target, decision code, tool name, evidence refs.
7. Không commit `.env`, API key, input của cuộc thi, output, trace, hay file debug.
8. **Không tự chạy `day09 run` trên toàn bộ 100 case và không nộp bài** — mọi MCP call đều bị server audit. Khi cần thử với MCP thật, chỉ chạy trên 1–3 case và báo trước. Khoa sẽ tự chạy full run và nộp.
9. Không sửa logic nghiệp vụ trong `agents/order_agent.py`, `agents/shipment_agent.py` (của Sơn) và `agents/payment_agent.py`, `agents/policy_agent.py` (của Đạt) ngoài việc tạo skeleton ban đầu ở Task 1.

## Quy ước làm việc

- Làm trên branch `feat/core-khoa`, commit nhỏ theo từng task, message dạng `feat: ...` / `test: ...` / `docs: ...`.
- Giữ style code hiện có: `from __future__ import annotations`, type hints đầy đủ, dataclass, không thêm framework agent nặng (LangChain, CrewAI…) — không cần và không được chấm điểm.
- Sau mỗi task chạy `pytest -q` (và `ruff`/lint nếu `pyproject.toml` có cấu hình). Không để test cũ bị hỏng.
- Kết thúc mỗi task, tóm tắt: file đã sửa, quyết định thiết kế, cái gì chưa làm, câu hỏi cần Khoa quyết.

---

## Task 1 — Interface chung + skeleton (ưu tiên cao nhất, Sơn và Đạt đang chờ)

Tạo package `src/student_agent/agents/` với:

**`agents/base.py`**

- `EvidenceRecord` (frozen dataclass): `evidence_ref`, `result_hash`, `domain`, `tool_name`, `data`, `warnings`.
- `EvidenceStore` — **một instance mới cho mỗi case**:
  - `__init__(case_id, gateway, trace)`
  - `async fetch(actor: str, tool_name: str, **arguments) -> EvidenceRecord`: gọi `gateway.call(tool_name, case_id=self.case_id, **arguments)`, lưu record, emit trace `tool_result_consumed` (actor, `tool_name`, `evidence_refs=[ref]`). Có retry giới hạn (tối đa 2 lần, backoff ngắn) cho lỗi timeout/mạng; **không** retry lỗi not-found.
  - `owns(ref) -> bool`, `records(domain=None)`, `refs()`.
  - Kiểm tra quyền: nhận một `allowed_tools: set[str]` theo actor; actor gọi tool ngoài quyền → raise lỗi.
- `AgentMessage` (A2A envelope): `case_id`, `sender`, `recipient`, `task`, `payload: dict`, `evidence_refs: list[str]`, `message_id`.
- `send(message, trace)`: emit trace `handoff` (actor=sender, target=recipient, `decision_code`=task).
- `SpecialistResult`: `actor`, `findings: dict[str, Any]` (tín hiệu đã chuẩn hoá, vd. `order_status`, `paid_total_brl`…), `candidate_issues: list[tuple[str, float]]` (primary_issue + điểm tin cậy), `entities: dict[str, list[str]]` (theo key của `affected_entities`), `cause_codes`, `responsible_parties`, `refund_lines`, `evidence_refs: list[str]` (chỉ ref thật sự hỗ trợ finding), `errors: list[str]`.
- `class Specialist(Protocol)`: `name: str`, `allowed_tools: set[str]`, `async run(case, store, trace) -> SpecialistResult`.

**Skeleton** (chỉ khung, trả `SpecialistResult` rỗng + TODO rõ ràng cho chủ sở hữu):
`agents/order_agent.py` (Sơn), `agents/shipment_agent.py` (Sơn), `agents/payment_agent.py` (Đạt), `agents/policy_agent.py` (Đạt). Thêm docstring ngắn nêu chủ sở hữu và các `primary_issue` họ phụ trách (xem bảng trong `PLAN.md`).

**Test:** `tests/test_agents_base.py` với fake gateway (không gọi MCP thật): store tách biệt giữa 2 case, `fetch` emit đúng trace, tool ngoài quyền bị chặn, retry có giới hạn.

## Task 2 — Coordinator trong `workflow.py`

`solve_case` thực hiện:

1. Tạo `EvidenceStore` mới cho case.
2. Coordinator emit `task_assigned` cho từng specialist (target = tên specialist).
3. Chạy các specialist (có thể `asyncio.gather`), mỗi specialist bọc try/except: lỗi → `SpecialistResult` rỗng có `errors`, không làm sập case.
4. Mỗi specialist gửi kết quả về coordinator qua `AgentMessage` → trace `handoff`.
5. Chuyển findings tổng hợp cho policy agent (handoff coordinator → policy), policy emit `policy_decided` với `decision_code`.
6. Tổng hợp output: chọn `primary_issue` từ các `candidate_issues` (điểm cao nhất; hoà hoặc không có → `insufficient_evidence`), hợp nhất entities, causes (rank lại 1..n), responsible parties, refund lines, `claim_assessments`, `data_conflicts` (khi findings của các nguồn lệch nhau, hoặc số trong customer message lệch với MCP — ưu tiên MCP).
7. Handoff coordinator → verifier, verifier emit `verification_completed`.
8. Trả dict đúng schema `day09-l3a-output-v2`.

Thứ tự event mỗi case phải là: (`case_received` từ CLI) → `task_assigned` → `tool_result_consumed`… → `handoff` → `policy_decided` → `verification_completed` → (`case_finalized` từ CLI). Trace phải thể hiện **ít nhất 3 actor khác nhau** tương tác.

## Task 3 — Verifier (`agents/verifier.py`)

Hàm thuần (dễ test) `verify(output, store) -> VerificationReport` kiểm tra, và một bước `repair` an toàn:

- Output pass JSON Schema (dùng `Contracts` có sẵn).
- `output["case_id"] == case["case_id"]`.
- Mọi ref trong `evidence_refs` và `claim_assessments[*].evidence_refs` ∈ `store.refs()` và đã có event `tool_result_consumed` tương ứng trong case.
- `recommended_refund_brl == sum(refund_lines.amount_brl)` (so sánh bằng `Decimal`, làm tròn 2 chữ số); refund không vượt số đã trả (nếu biết).
- `case_status == "no_action"` ⇒ refund = 0 và không có action hoàn tiền; `action_required` ⇒ có ít nhất 1 action.
- `resolution_actions` không trùng; `responsible_parties` loại `seller` phải có `party_id` nằm trong `affected_entities.seller_ids`.
- ID trong `affected_entities` phải xuất hiện trong data của evidence của case.
- `evidence_refs` top-level chỉ giữ ref được dùng bởi finding đã chọn (precision), tối đa 30.

Nếu vi phạm không sửa an toàn được → hạ về `primary_issue="insufficient_evidence"`, `case_status="needs_investigation"`, refund 0, confidence thấp; **không** bịa dữ liệu để "vá". Trace `verification_completed` có `decision_code` `PASS` / `REPAIRED` / `DOWNGRADED` và `attributes` đếm số lỗi.

**Test:** `tests/test_verifier.py` — ref lạ bị loại/hạ cấp, tổng tiền lệch bị bắt, status/refund mâu thuẫn bị bắt, output hợp lệ giữ nguyên.

## Task 4 — Calibration + failure policy

- `confidence` trong `assessment` = hàm xác định (deterministic) của: điểm candidate cao nhất, khoảng cách với candidate thứ 2, số evidence bắt buộc thiếu, có `data_conflicts` hay không, verifier có phải repair/downgrade không. Gợi ý khởi điểm: rõ ràng + đủ evidence ≈ 0.85–0.95; có conflict ≈ 0.6; thiếu dữ liệu ≈ 0.3. Đặt các hằng số ở một chỗ để dễ tinh chỉnh sau khi xem điểm.
- Failure policy theo bảng trong `PLAN.md` (timeout → retry giới hạn; not found → không retry, đánh dấu thiếu; source conflict → ghi `data_conflicts`; specialist lỗi → verifier hạ cấp). Ghi lỗi vào trace bằng `decision_code`/`attributes`, không ghi stack trace dài.
- (Tuỳ chọn) chạy song song nhiều case với semaphore giới hạn (4–8) — chỉ làm nếu không phá vỡ thứ tự event trong từng case và không đổi hành vi CLI hiện tại; nếu phải sửa `cli.py`, giải thích lý do.

## Task 5 — `ARCHITECTURE.md`

Điền đủ 7 mục theo template, khớp với code thật: sơ đồ luồng, bảng agent ownership (actor ↔ tool được phép gọi ↔ handoff), A2A envelope + điều kiện handoff + chống vòng lặp, evidence lifecycle, bảng failure policy, danh sách invariant của verifier, reproducibility (phiên bản Python, dependency, concurrency, lệnh chạy). Không ghi API key hay prompt bí mật. Phần chi tiết của agent Sơn/Đạt để placeholder `TODO(Sơn)` / `TODO(Đạt)`.

---

## Tiêu chí hoàn thành

- [ ] `pytest -q` pass, không gọi MCP thật trong test.
- [ ] Với fake gateway, `solve_case` trả output pass `contracts.validate_output` và trace pass schema, đủ event bắt buộc đúng thứ tự.
- [ ] Grep toàn repo không có chuỗi `ev_` nào được hard-code/ghép tạo ra trong source (ngoài test fixture).
- [ ] Không có file `.env`, input, output, trace hay `dist/` trong commit.
- [ ] Sơn và Đạt chỉ cần điền logic vào `run()` của agent mình, không phải sửa coordinator.
