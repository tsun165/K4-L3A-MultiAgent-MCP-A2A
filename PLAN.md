# Kế hoạch triển khai — K4 L3A Multi-Agent MCP + A2A

> Mục tiêu: hoàn thiện `solve_case()` trong `src/student_agent/workflow.py` để điều tra 100 case khiếu nại TMĐT (dữ liệu kiểu Olist), lấy evidence thật qua MCP Gateway, phối hợp nhiều agent, sinh `outputs/<case_id>.json` + `traces/trace.jsonl` đúng contract, rồi đóng gói nộp.

---

## 0. Hiện trạng repo

| Thành phần | Trạng thái |
| --- | --- |
| CLI `day09` (`validate-inputs`, `mcp-tools`, `run`, `validate`, `package`) | ✅ Có sẵn |
| `EvidenceGateway.call()` — gọi tool MCP, validate evidence envelope | ✅ Có sẵn |
| `TraceWriter.emit()` — ghi trace, validate schema | ✅ Có sẵn |
| CLI tự emit `case_received` (trước) và `case_finalized` (sau) mỗi case | ✅ Có sẵn |
| `solve_case()` | ❌ `NotImplementedError` — **việc chính** |
| `ARCHITECTURE.md` | ❌ Toàn `TODO` — phải điền |
| `.env`, `case-set.json`, `inputs/*.json` | ❌ Chưa có — cần đăng ký team + tải release |

---

## Phân công

| Người | GitHub | Phụ trách | File chính |
| --- | --- | --- | --- |
| **Khoa** (trưởng nhóm) | `dokhacgiakhoa` | Khung multi-agent (EvidenceStore, A2A envelope, trace), coordinator, verifier, calibration, failure policy, `ARCHITECTURE.md`, chạy + đóng gói + nộp | `workflow.py`, `agents/base.py`, `agents/verifier.py`, `ARCHITECTURE.md` |
| **Sơn** | `tsun165` | Order/item/seller agent + shipment agent; rule cho `canceled_order_paid`, `unavailable_order_paid`, `late_delivery_seller`, `late_delivery_logistics`; điền `affected_entities` | `agents/order_agent.py`, `agents/shipment_agent.py`, phần order/shipment trong `rules.py` |
| **Đạt** | `liber72` | Payment/refund agent + policy agent; rule cho `valid_split_payment`, `payment_mismatch`, `duplicate_charge`, `refund_pending`, `refund_failed`; tính `financial_resolution` | `agents/payment_agent.py`, `agents/policy_agent.py`, phần payment/refund trong `rules.py` |

> Phần việc của Khoa được giao cho **Antigravity** (AI agent) thực hiện; Khoa review, chạy full run và nộp bài.

Việc chung cả nhóm: `unsupported_claim` / `insufficient_evidence`, `claim_assessments`, `data_conflicts`, review chéo và test.

**Cách làm việc:**

- Mỗi người làm trên branch riêng (`feat/core-khoa`, `feat/order-shipment-son`, `feat/payment-policy-dat`), merge vào `main` qua PR, ít nhất 1 người khác review.
- Khoa chốt **interface** (`AgentMessage`, `EvidenceStore`, kiểu kết quả specialist trả về) trong ngày 1. Sơn và Đạt code theo interface đó, không cần chờ coordinator xong.
- Mỗi specialist trả về `SpecialistResult` gồm: `findings` (các tín hiệu đã chuẩn hoá), `candidate_issues`, `evidence_refs` đã dùng. Coordinator và verifier (Khoa) tổng hợp thành output.
- Chỉ Khoa chạy `day09 run` trên toàn bộ 100 case và nộp bài, để trace/audit không bị lẫn giữa nhiều lần chạy. Sơn và Đạt thử trên vài case.
- Mỗi người tự quản lý `.env` của mình, không commit key.

---

## 1. Thang điểm → ưu tiên

| Thành phần | Trọng số | Ý nghĩa thực tế | Ưu tiên |
| --- | ---: | --- | --- |
| `semantic` | 45% | `primary_issue`, `case_status`, entities, refund, responsible party… khớp oracle | 🔴 Cao nhất |
| `evidence` | 15% | Có đủ nhóm evidence bắt buộc + không trích evidence thừa/không liên quan | 🔴 |
| `provenance` | 15% | Mọi `evidence_ref` phải lấy từ MCP, đúng team/run/case | 🔴 (hard gate) |
| `consistency` | 10% | status ↔ refund ↔ action, seller responsibility, không trùng action | 🟠 |
| `schema` | 5% | Pass JSON Schema | 🟢 dễ, làm ngay |
| `calibration` | 5% | `confidence` ≈ xác suất `primary_issue` đúng | 🟠 |
| `workflow` | 5% | Đủ 5 event bắt buộc, đúng thứ tự, nhiều actor, evidence trong trace khớp output | 🟢 dễ |
| `efficiency` | 0% | Không tính điểm nhưng vẫn audit | ⚪ |

**Hard gates (case = 0 điểm):** sai `case_id`, output không chấm được theo schema, thiếu evidence bắt buộc, evidence ref không tồn tại / bị sửa / thuộc case-run-team khác.

→ **Nguyên tắc vàng:** chỉ dùng `evidence_ref` nhận trực tiếp từ `gateway.call()` **trong chính case đó**; không đoán, không cache chéo case; thiếu dữ liệu thì trả `insufficient_evidence` / `needs_investigation` chứ không bịa.

---

## 2. Các giai đoạn

### Giai đoạn 1 — Setup & khám phá (ngày 1) — cả nhóm

- [ ] **Cả 3:** tạo venv Python 3.11, `pip install -e ".[dev]"`, `pytest -q` pass.
- [ ] **Khoa:** đăng ký team trên `/register`, chia sẻ `sk-team-...` cho nhóm qua kênh riêng (không commit, không dán vào issue/PR).
- [ ] **Khoa:** tải ZIP input L3A từ GitHub Release, `day09 validate-inputs` → `OK: l3a / ... / 100 cases`, chia sẻ cho nhóm.
- [ ] **Khoa:** `day09 mcp-tools` → ghi lại danh sách tool thực tế (tên, tham số) vào `notes/mcp-tools.md`.
- [ ] Khám phá dữ liệu (script để trong `scripts/`, không nộp):
  - **Khoa:** đọc ~10–15 case input — các field có gì (customer message, order_id, claim…)?
  - **Sơn:** gọi thử tool domain `order`, `item`, `seller`, `product`, `shipment` cho 2–3 case, dump `data` + `warnings`.
  - **Đạt:** gọi thử tool domain `payment`, `refund`, `policy`, `customer` cho 2–3 case, dump `data` + `warnings`.
  - ⚠️ Mọi call đều bị audit → khám phá có chừng mực, dùng đúng `case_id`.
- [ ] **Sơn + Đạt:** ghi mapping domain ↔ tool ↔ field quan trọng của phần mình vào `notes/mcp-tools.md`.

**Deliverable:** `notes/mcp-tools.md` + cả nhóm hiểu rõ input format.

### Giai đoạn 2 — Khung multi-agent + trace (ngày 1–2) — Khoa chính

Cấu trúc code đề xuất (tách file trong `src/student_agent/`):

```text
workflow.py        # solve_case(): coordinator điều phối
agents/
  base.py          # AgentMessage (A2A envelope), EvidenceStore theo case
  order_agent.py   # get_order / items / seller / product
  payment_agent.py # payments, refunds
  shipment_agent.py# shipment, ngày giao dự kiến vs thực tế
  policy_agent.py  # policy → quyết định refund/action
  verifier.py      # kiểm tra invariants trước finalize
rules.py           # bảng quyết định primary_issue / cause / responsible
```

- [ ] **Khoa:** **EvidenceStore** (khởi tạo mới mỗi case): lưu `{evidence_ref, domain, tool, data, warnings}`; chỉ nó được phép cấp ref cho output.
- [ ] **Khoa:** **A2A envelope**: `{case_id, from, to, task, payload, evidence_refs}`; mỗi handoff emit trace `handoff` (actor → target).
- [ ] **Khoa:** định nghĩa `SpecialistResult` + skeleton rỗng cho 4 specialist để Sơn/Đạt điền.
- [ ] **Khoa:** **Phân quyền tool**: mỗi specialist chỉ gọi tool thuộc domain của mình.
- [ ] **Sơn / Đạt:** trong agent của mình, gọi tool qua EvidenceStore và emit `tool_result_consumed` mỗi khi dùng evidence.
- [ ] Trace mỗi case theo thứ tự:
  1. `case_received` (CLI đã emit)
  2. `task_assigned` coordinator → từng specialist
  3. `tool_result_consumed` mỗi khi dùng evidence (kèm `tool_name`, `evidence_refs`)
  4. `handoff` specialist → coordinator / → policy / → verifier
  5. `policy_decided` (policy-agent, `decision_code`)
  6. `verification_completed` (verifier, `decision_code=PASS/FAIL`)
  7. `case_finalized` (CLI đã emit)
- [ ] Không ghi prompt / chain-of-thought vào trace, chỉ ghi decision code.

**Deliverable:** `day09 run` chạy hết 100 case với output tối thiểu pass schema (`primary_issue=insufficient_evidence`, confidence thấp) nhưng dùng evidence thật; `day09 validate` pass.

### Giai đoạn 3 — Logic nghiệp vụ (ngày 2–4) — trọng tâm 45%, Sơn + Đạt chính

Xây bảng quyết định cho 11 giá trị `primary_issue`:

| `primary_issue` | Tín hiệu cần kiểm | Evidence chính | Hướng xử lý gợi ý | Owner |
| --- | --- | --- | --- | --- |
| `canceled_order_paid` | order status = canceled nhưng payment đã capture, chưa refund | order, payment, refund | `action_required`, refund toàn bộ số đã trả | Sơn |
| `unavailable_order_paid` | status = unavailable, đã thanh toán | order, payment, refund | `action_required`, refund | Sơn |
| `late_delivery_seller` | giao trễ do seller giao cho carrier trễ (`shipping_limit_date`) | shipment, order, seller | responsible = seller | Sơn |
| `late_delivery_logistics` | seller đúng hạn nhưng carrier giao trễ so với estimated | shipment, order | responsible = logistics_provider | Sơn |
| `valid_split_payment` | nhiều payment (voucher + card…) tổng khớp order | payment, order | `no_action`, claim unsupported | Đạt |
| `payment_mismatch` | tổng payment ≠ tổng giá + freight | payment, item, order | refund phần chênh | Đạt |
| `duplicate_charge` | 2 payment giống hệt (cùng số tiền/method/sequential bất thường) | payment | refund khoản trùng | Đạt |
| `refund_pending` | có refund nhưng đang pending | refund, payment | theo dõi / `needs_investigation` hoặc action | Đạt |
| `refund_failed` | refund thất bại | refund, payment | re-issue refund | Đạt |
| `unsupported_claim` | dữ liệu mâu thuẫn với claim khách | domain liên quan | `no_action`, refund 0 | Khoa (tổng hợp) |
| `insufficient_evidence` | tool not found / thiếu dữ liệu | những gì có | `needs_investigation` | Khoa (tổng hợp) |

`canceled_order_paid` / `unavailable_order_paid` cần cả tín hiệu order (Sơn) lẫn payment/refund (Đạt): Sơn viết rule, Đạt cung cấp số tiền đã trả / đã hoàn.

> Bảng trên là giả thuyết — **phải kiểm chứng lại với dữ liệu thật ở Giai đoạn 1** (tên field, enum status, cấu trúc policy).

Các việc cụ thể:

- [ ] **Đạt:** chuẩn hoá tiền tệ: dùng `Decimal`, làm tròn 2 chữ số; `recommended_refund_brl == sum(refund_lines.amount_brl)`.
- [ ] **Sơn:** `affected_entities`: chỉ đưa ID xuất hiện trong evidence của case (không lấy từ customer message nếu MCP không xác nhận). Đạt bổ sung `payment_references`.
- [ ] **Khoa:** `claim_assessments`: tách các claim trong message khách → verdict + evidence riêng.
- [ ] **Sơn + Đạt:** `root_cause_analysis.ranked_causes`: cause code UPPER_SNAKE, rank 1..n; `responsible_parties` nhất quán với issue (mỗi người phần issue của mình).
- [ ] **Khoa:** `data_conflicts`: khi 2 nguồn lệch nhau (vd. message nói 200 BRL, payment nói 150) → ghi `field`, `sources`, `selected_source` (ưu tiên MCP), `resolution_code`.
- [ ] **Đạt:** `resolution_actions`: bộ action code cố định, không trùng, khớp status (`no_action` ⇒ không có action refund). Chốt danh sách action code với cả nhóm.
- [ ] **Đạt:** policy agent đọc policy từ MCP (nếu có tool policy) để quyết định cửa sổ refund / trách nhiệm.
- [ ] **Khoa:** `evidence_refs` top-level: chỉ những ref **thực sự hỗ trợ kết luận** (precision quan trọng — đừng dump mọi ref đã gọi).

### Giai đoạn 4 — Verifier & calibration (ngày 4) — Khoa chính

Verifier kiểm trước khi trả output:

- [ ] Schema pass (`contracts.validate_output`).
- [ ] Mọi ref trong output ⊆ EvidenceStore của case hiện tại.
- [ ] Mỗi ref trong output đều đã xuất hiện trong trace `tool_result_consumed` của case.
- [ ] Tổng tiền khớp; refund ≤ số tiền đã trả.
- [ ] `case_status` ↔ refund ↔ actions nhất quán (vd. `no_action` ⇒ refund = 0).
- [ ] `responsible_parties` seller có `party_id` = seller_id trong evidence.
- [ ] Nếu fail → hạ về `needs_investigation` + confidence thấp, không bịa.

Calibration:

- [ ] Confidence theo độ chắc của rule: ví dụ ~0.9 khi tín hiệu rõ + đủ evidence, ~0.6 khi có conflict, ~0.3 khi thiếu dữ liệu. Điều chỉnh sau khi xem điểm public.

### Giai đoạn 5 — Failure policy & độ bền (ngày 4–5) — Khoa chính, Sơn/Đạt xử lý lỗi trong agent của mình

| Failure | Retry? | Fallback | Trace |
| --- | --- | --- | --- |
| MCP timeout / lỗi mạng | Có, tối đa 2 lần, backoff | Bỏ domain đó, giảm confidence | `attributes.error=timeout` |
| Not found | Không | `insufficient_evidence` nếu là evidence bắt buộc | `decision_code=NOT_FOUND` |
| Source conflict | Không | Ưu tiên nguồn MCP có thẩm quyền, ghi `data_conflicts` | `decision_code=SOURCE_CONFLICT` |
| Specialist trả kết quả không hợp lệ | Không | Verifier đánh `needs_investigation` | `verification_completed` FAIL |

- [ ] Một case lỗi không được làm sập cả run (bắt exception trong `solve_case`, vẫn trả output hợp lệ từ evidence đã có).
- [ ] Cân nhắc chạy song song có giới hạn (semaphore 4–8) nếu 100 case chạy chậm — lưu ý CLI hiện chạy tuần tự.

### Giai đoạn 6 — Test, tài liệu, nộp bài (ngày 5)

- [ ] **Sơn:** unit test cho rule order/shipment với fixture data giả (chỉ để test logic, **không** dùng làm output).
- [ ] **Đạt:** unit test cho rule payment/refund/policy + tính tiền.
- [ ] **Khoa:** test verifier: ref lạ bị chặn, tổng tiền lệch bị bắt.
- [ ] **Khoa** (Sơn/Đạt viết phần agent của mình): điền đầy đủ `ARCHITECTURE.md` (7 mục: overview, ownership, A2A, evidence lifecycle, failure policy, invariants, reproducibility).
- [ ] **Khoa:** `day09 run` → `day09 validate` → `day09 package --output dist/submission.zip`.
- [ ] **Khoa:** kiểm tra ZIP chỉ gồm `manifest.json`, `trace.jsonl`, `outputs/*.json` (không source, `.env`, key, log).
- [ ] **Khoa:** upload tại `/l3a`, chia sẻ breakdown điểm public cho nhóm → quay lại Giai đoạn 3–4 tinh chỉnh.

---

## 3. Vòng lặp cải thiện sau lần nộp đầu

1. Nhìn component điểm thấp nhất trong breakdown public.
2. `semantic` thấp → rà lại bảng quyết định, đọc lại các case bị phân loại mơ hồ (**Sơn / Đạt** theo issue mình phụ trách).
3. `evidence` thấp → thiếu nhóm bắt buộc (gọi thêm tool) hoặc thừa ref (cắt bớt) (**Sơn / Đạt** theo domain, **Khoa** phần chọn ref top-level).
4. `consistency` thấp → siết verifier (**Khoa**).
5. `calibration` thấp → chỉnh bảng confidence (**Khoa**).
6. Nhớ: public chỉ chiếm 20% điểm cuối, private 80% → **không overfit** theo public, ưu tiên rule tổng quát.

---

## 4. Checklist an toàn (đọc trước mỗi lần nộp)

- [ ] Không có `evidence_ref` tự tạo / sửa / dùng chéo case.
- [ ] Mọi case có `case_id` đúng.
- [ ] `.env` và API key không nằm trong git hay ZIP.
- [ ] Trace có đủ 5 event bắt buộc cho **mọi** case, đúng thứ tự.
- [ ] Customer message không được coi là ground truth.
