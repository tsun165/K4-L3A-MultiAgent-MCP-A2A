# L3A Architecture Record

Hệ thống điều tra khiếu nại thương mại điện tử Multi-Agent sử dụng MCP Evidence Gateway và giao thức Agent-to-Agent (A2A).

## 1. System overview

Luồng xử lý từ input case đến output và trace audit:

```text
[inputs/<case_id>.json]
         │
         ▼ (CLI: case_received)
   ┌─────────────┐
   │ Coordinator │ ──(task_assigned)──► [OrderAgent] ────┐
   │             │ ──(task_assigned)──► [ShipmentAgent] ─┤
   │             │ ──(task_assigned)──► [PaymentAgent] ──┤ (asyncio.gather)
   └──────┬──────┘                                       │
          │                                              ▼
          │◄──────────(handoff: specialist results)──────┘
          │
          ├──(task_assigned)──► [PolicyAgent] ──► (policy_decided)
          │◄──(handoff)───────────────┘
          │
          ▼ (Candidate Output Synthesis)
          │
          └──(handoff)──► [Verifier]
                              │
                              ├── (verify_and_repair: 8 invariants)
                              ├── (trace: verification_completed [PASS/REPAIRED/DOWNGRADED])
                              ▼
                      [Final Output] ──► (CLI: case_finalized & outputs/<case_id>.json)
```

## 2. Agent ownership

Danh sách 10 tool MCP thật (verify bằng `day09 mcp-tools` + `input_schema` của từng tool, 2026-09-25): `get_order`, `get_order_items`, `get_sellers`, `get_product_context`, `get_shipment_summary`, `get_order_payments`, `get_payment_timeline`, `get_refund_timeline`, `get_policy`, `get_customer_history`. Mọi tool nhận `case_id`; tham số riêng là `order_id` (hầu hết), `policy_version` (`get_policy`), `customer_unique_id` (`get_customer_history`, chưa dùng).

| Actor | Input | Trách nhiệm | Output / Handoff | Allowed Tools |
| --- | --- | --- | --- | --- |
| `coordinator` | `case` | Khởi tạo `EvidenceStore`, đăng ký quyền tool cho từng actor, chia việc song song, tổng hợp findings thành `primary_issue`/`case_status`/`resolution_actions` (bảng cố định `_ACTION_MAP` trong `workflow.py`), quyết định `unsupported_claim` khi mọi claim bị evidence phủ định | Handoff sang `policy-agent` (chỉ để lấy policy) và `verifier` | Không gọi tool trực tiếp |
| `order-agent` | `case`, `EvidenceStore` | Điều tra đơn hàng/mặt hàng/người bán (`order_ids`, `item_ids`, `seller_ids`), rule `canceled_order_paid`, `unavailable_order_paid` theo `order_status`; `apply_payment_totals()` cộng thêm refund line từ số liệu payment-agent | `SpecialistResult` → `coordinator` | `get_order`, `get_order_items`, `get_sellers` |
| `shipment-agent` | `case`, `EvidenceStore` | So sánh `delivered_carrier_at` với `shipping_limit_at` (chọn dòng hợp lý khi có nhiều bản ghi mâu thuẫn) và `delivered_customer_at` với `estimated_delivery_at`; rule `late_delivery_seller`, `late_delivery_logistics`; hoàn `freight_value` của item trễ | `SpecialistResult` → `coordinator` | `get_shipment_summary`, `get_order_items`, `get_order` |
| `payment-agent` | `case`, `EvidenceStore` | Đối soát thanh toán/hoàn tiền (`payment_references`, `paid_total_brl`, `refunded_total_brl` cho order-agent dùng); rule `valid_split_payment`, `payment_mismatch`, `duplicate_charge`, `refund_pending`, `refund_failed` — **chỉ xét đúng topic khách hàng claim**, topic khác không được suy diễn từ dữ liệu nền không liên quan | `SpecialistResult` → `coordinator` | `get_order_payments`, `get_payment_timeline`, `get_refund_timeline`, `get_order_items` |
| `policy-agent` | `case` | **Chỉ fetch** `get_policy(policy_version)`, trả về `findings["policy_raw"]`. Không tự đoán `case_status`/`resolution_actions` từ raw claim topic (vì đó chưa phải kết luận đã verify) — coordinator mới là nơi quyết định, dựa trên `primary_issue` đã được specialist xác nhận bằng evidence | `SpecialistResult` → `coordinator`; coordinator emit `policy_decided` với actor=`policy-agent` | `get_policy` |
| `verifier` | Candidate output, `EvidenceStore` | Kiểm định invariants, auto-repair không sai lệch, safe downgrade nếu phát hiện lỗi cấu trúc, tái tính `confidence` qua `calibration.calculate_confidence` | `final_output` đúng JSON schema, emit `verification_completed` | Không gọi tool |

## 3. A2A protocol

Giao thức trao đổi tin cậy giữa các agent:
- **Envelope (`AgentMessage`)**:
  - `case_id`: ID case điều tra (chống dùng chéo scope).
  - `sender`: Tên actor gửi tin.
  - `recipient`: Tên actor nhận tin.
  - `task`: Mã công việc / quyết định nghiệp vụ (ghi vào `decision_code` của trace).
  - `payload`: Dữ liệu findings có cấu trúc.
  - `evidence_refs`: Danh sách các bằng chứng hỗ trợ cho thông điệp.
  - `message_id`: Định danh duy nhất `msg_<token>`.
- **Trace Handoff**: Mỗi khi gửi tin qua hàm `send(message, trace)`, một sự kiện `handoff` được ghi vào `trace.jsonl` với `actor=sender`, `target=recipient`, `decision_code=task`.
- **Chống vòng lặp**: Luồng A2A là Directed Acyclic Graph (DAG) cố định: `Coordinator` $\to$ `Specialists` $\to$ `Coordinator` $\to$ `Policy` $\to$ `Coordinator` $\to$ `Verifier`. Không có vòng hồi quy (loop-free).
- **Correlation**: Mọi thông điệp và trace event đều gắn chặt với `case_id`.

## 4. Evidence lifecycle

- **Khởi tạo độc lập**: Mỗi case sở hữu một instance `EvidenceStore` riêng biệt được tạo mới bởi `Coordinator`. Tuyệt đối không có cache toàn cục hoặc chia sẻ evidence giữa các case.
- **Autoritative Gateway Call**: Chỉ gọi qua `gateway.call(...)`. Gateway kiểm tra schema response `mcp-evidence-response-v1`.
- **Kiểm soát quyền truy cập**: `EvidenceStore` kiểm tra `allowed_tools` của từng actor trước khi gọi gateway. Actor gọi tool ngoài quyền sẽ bị chặn ngay (`PermissionError`).
- **Audit Logging**: Ngay khi nhận được evidence hợp lệ, `EvidenceStore` ghi nhận record và phát sự kiện `tool_result_consumed` với `actor`, `tool_name`, `evidence_refs=[record.evidence_ref]`.
- **Lưu trữ & Truy xuất**: Lưu mapping `evidence_ref` $\to$ `EvidenceRecord`. Phương thức `owns(ref)` cho phép Verifier kiểm tra tính chính danh của bằng chứng.

## 5. Failure policy

| Failure | Retry? | Fallback | Trace event / code |
| --- | --- | --- | --- |
| MCP timeout / mạng chập chờn | Có (tối đa 2 lần, exponential backoff trong `EvidenceStore.fetch`) | Bỏ qua domain đó, ghi nhận lỗi vào `errors`, tiếp tục case | `attributes.violations_count` |
| Not found / tool báo lỗi vì domain không có dữ liệu (vd. `get_refund_timeline` báo lỗi khi order chưa từng có refund — quan sát thật) | Không retry (fail-fast) | Bọc riêng lệnh gọi đó trong try/except, coi là "không có dữ liệu" (list/dict rỗng), **không** để lỗi này làm mất các finding đã tính trước đó (vd. `paid_total_brl`) | `errors` của `SpecialistResult` ghi rõ tool + lỗi |
| Tool ngoài quyền của actor | Không retry | `EvidenceStore.check_permission` fail-closed: actor chưa đăng ký (`register_actor_tools`) coi như không có quyền gì, raise `PermissionError` ngay | Exception bị specialist's `run()` bắt, ghi vào `errors` |
| Source conflict (Message vs MCP) | Không retry | Luôn ưu tiên dữ liệu từ MCP authoritative, ghi nhận vào `data_conflicts` | `decision_code="SOURCE_CONFLICT"` |
| Specialist runtime exception | Không retry | Specialist trả về `SpecialistResult` rỗng có `errors`, không làm sập luồng chung | `actor_completed` kèm payload errors |
| Verifier invariant check failure | Không retry | Tự động vá an toàn (auto-repair); nếu lỗi schema cấu trúc thì hạ cấp (safe downgrade) | `verification_completed` (`REPAIRED` / `DOWNGRADED`) |

**Bài học thật gặp phải khi tích hợp MCP:**
- `mcp_gateway.py` gọi `result.isError`, nhưng bản `mcp>=2,<3` cài về (`2.2.0`) trả field `is_error` — đã vá tương thích ngược (đọc cả hai tên, giữ style fallback có sẵn của `structuredContent`/`structured_content`).
- Bug nghiêm trọng nhất phát hiện sau khi chạy full 100 case lần đầu: `payment-agent` kiểm tra `refund_failed`/`refund_pending`/`duplicate_charge` **không điều kiện** theo bất kỳ payment topic nào được claim, khiến một case chỉ claim `valid_split_payment` nhưng có sẵn 1 event refund "failed" không liên quan trong dữ liệu nền bị nhận nhầm thành `refund_failed`. Sau khi sửa để mỗi rule chỉ xét đúng topic được claim, `valid_split_payment`/`payment_mismatch`/`unsupported_claim` mới xuất hiện đúng trong kết quả.

## 6. Verification invariants

Verifier kiểm tra độc lập 8 invariants bắt buộc trước khi phê duyệt output:
1. **Schema Compliance**: Output pass 100% JSON Schema `day09-l3a-output-v2` (`additionalProperties: false`).
2. **Case Isolation**: `output["case_id"] == store.case_id`.
3. **Evidence Provenance & Precision**: Mọi ref trong `evidence_refs` và `claim_assessments[*].evidence_refs` phải nằm trong `store.refs()`. Bất kỳ ref lạ nào đều bị loại bỏ ngay lập tức. Top-level refs giới hạn tối đa 30.
4. **Financial Consistency**: `recommended_refund_brl == sum(refund_lines.amount_brl)` tính bằng `Decimal` làm tròn 2 chữ số (`ROUND_HALF_UP`).
5. **Status $\leftrightarrow$ Action $\leftrightarrow$ Refund Harmony**:
   - `case_status == "no_action"` $\implies$ refund = 0.0, `refund_lines = []`, không có bất kỳ action hoàn tiền nào trong `resolution_actions`.
   - `case_status == "action_required"` $\implies$ ít nhất 1 action hợp lệ trong `resolution_actions`.
6. **Action Set Constraints**: `resolution_actions` là tập hợp không trùng lặp (`uniqueItems: true`), độ dài mỗi action từ 1 đến 80 ký tự, tối đa 8 actions.
7. **Entity & Party Consistency**:
   - Mọi `responsible_parties` loại `seller` phải có `party_id` nằm trong `affected_entities["seller_ids"]`.
   - `ranked_causes` có rank từ 1 đến n (tối đa 5), mã lỗi `cause_code` theo chuẩn `^[A-Z][A-Z0-9_]{2,79}$`.
8. **Safe Downgrade Policy**: Nếu output bị lỗi cấu trúc nghiêm trọng không thể vá an toàn, Verifier tự động hạ cấp xuống:
   - `primary_issue = "insufficient_evidence"`
   - `case_status = "needs_investigation"`
   - `confidence = 0.20`
   - `recommended_refund_brl = 0.0`, `refund_lines = []`
   - `resolution_actions = ["manual_investigation_required"]`

## 7. Reproducibility

- **Ngôn ngữ & Runtime**: Python 3.11+.
- **Quản lý Dependencies**: Cài đặt qua `pyproject.toml` (`httpx2>=2,<3`, `jsonschema[format]>=4.25,<5`, `mcp>=2,<3`, `python-dotenv>=1.1,<2`).
- **Không dùng framework ngoài**: Toàn bộ luồng Multi-Agent xây dựng thuần túy bằng `asyncio`, `dataclasses`, `Protocol`, chuẩn hoá theo Clean Architecture.
- **Deterministic Calibration**: Hàm tính confidence xác định trong `calibration.py`, không sử dụng random.
- **Concurrency**: Hỗ trợ chạy async song song các specialists trong từng case bằng `asyncio.gather`.
- **Lệnh thực thi**:
  ```bash
  day09 mcp-tools      # Khám phá công cụ MCP Gateway
  day09 run            # Thực thi toàn bộ quy trình multi-agent
  day09 validate       # Kiểm định hợp đồng output và trace audit
  day09 package        # Đóng gói dist/submission.zip
  ```
