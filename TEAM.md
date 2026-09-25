# Danh Sách Thành Viên & Báo Cáo Phân Công Nhóm

- **Tên Nhóm:** `1nguoi1mang`
- **Mã Nhóm / Lớp:** `K4-L3A`
- **Tên Repository Nộp Bài:** [`K4-L3A-MultiAgent-MCP-A2A`](https://github.com/tsun165/K4-L3A-MultiAgent-MCP-A2A)

---

## # Thành viên

| STT | Họ và tên | MSSV | GitHub | Vai trò & Phân công công việc | Issue | Trạng thái |
|---:|---|---|---|---|---|:---:|
| 1 | Đỗ Khắc Gia Khoa (@Dokhacgiakhoa) | 02733 | [Dokhacgiakhoa](https://github.com/Dokhacgiakhoa) | Trưởng nhóm & Integrator — interface (`agents/base.py`), coordinator (`workflow.py`), tool registry (`agents/tools.py`), mã chuẩn (`agents/vocab.py`), điều phối MCP, chạy full run và nộp bài | [Issue #1](https://github.com/tsun165/K4-L3A-MultiAgent-MCP-A2A/issues/1) | ✅ |
| 2 | Đỗ Thái Sơn (@tsun165) | 03021 | [tsun165](https://github.com/tsun165) | Order/Item/Seller & Shipment agent (`agents/order_agent.py`, `agents/shipment_agent.py`) — rule `canceled_order_paid`, `unavailable_order_paid`, `late_delivery_seller`, `late_delivery_logistics` | [Issue #2](https://github.com/tsun165/K4-L3A-MultiAgent-MCP-A2A/issues/2) | ✅ |
| 3 | Hoàng Thái Đạt (@Liber72) | 02959 | [Liber72](https://github.com/Liber72) | Payment/Refund & Policy agent (`agents/payment_agent.py`, `agents/policy_agent.py`) — rule `valid_split_payment`, `payment_mismatch`, `duplicate_charge`, `refund_pending`, `refund_failed` | [Issue #3](https://github.com/tsun165/K4-L3A-MultiAgent-MCP-A2A/issues/3) | ✅ |
| 4 | Nguyễn Nguyên Phong (@Heargreaves1) | 02691 | [Heargreaves1](https://github.com/Heargreaves1) | Verifier & Calibration (`agents/verifier.py`, `calibration.py`) — kiểm định invariant, safe-downgrade, tính `confidence`; bộ test (`tests/`) | [Issue #8](https://github.com/tsun165/K4-L3A-MultiAgent-MCP-A2A/issues/8) | ✅ |1

---

## # Cá nhân

### ## Đỗ Khắc Gia Khoa - 02733
- **Vai trò:** Trưởng nhóm & Integrator.
- **Trạng thái:** ✅ Hoàn thành — merge toàn bộ vào `main`, chạy full run 100 case với MCP thật, đóng gói và nộp bài.
- **Công việc chi tiết đã hoàn thành:**
  - Định nghĩa interface dùng chung: `EvidenceStore` (cô lập evidence theo từng case), `AgentMessage`/`SpecialistResult` (giao thức A2A giữa các agent).
  - Viết `workflow.py` — coordinator điều phối 4 specialist, cầu nối payment→order (paid/refunded totals), quyết định `primary_issue` tập trung dựa trên evidence (kể cả nhánh `unsupported_claim`).
  - Xây dựng `agents/tools.py` (tool registry đã verify qua `day09 mcp-tools`) và `agents/vocab.py` (mã chuẩn UPPER_SNAKE dùng chung).
  - Vá 3 bug hạ tầng: `mcp_gateway.py` tương thích SDK `mcp>=2.2` (`is_error` thay vì `isError`), permission fail-open → fail-closed, thiếu `pytest-asyncio`.
  - Chạy `day09 run` nhiều lần để verify dữ liệu thật, phát hiện và điều phối sửa các bug nghiệp vụ (schema `get_refund_timeline` sai, dữ liệu nhiễu trùng dòng ở items/payments, xét nhầm topic không được claim).
  - Đóng gói `dist/submission.zip` và nộp trên workspace competition.
- **Điều học được / Đóng góp chính:**
  - Không có gì thay thế được việc verify với dữ liệu MCP thật — nhiều giả định ban đầu (tên field, cấu trúc response) chỉ lộ ra sai khi chạy full 100 case, không phải khi đọc tài liệu hay test đơn lẻ.

### ## Đỗ Thái Sơn - 03021
- **Vai trò:** Phụ trách Order/Item/Seller & Shipment agent.
- **Trạng thái:** ✅ Hoàn thành, merged ([PR #6](https://github.com/tsun165/K4-L3A-MultiAgent-MCP-A2A/pull/6)).
- **Công việc chi tiết đã hoàn thành:**
  - Triển khai `order_agent.py` — xác nhận `order_status` (`canceled`/`unavailable`) qua `get_order`, đối chiếu với claim của khách; `apply_payment_totals()` cộng thêm refund line từ số liệu payment-agent.
  - Triển khai `shipment_agent.py` — so sánh `delivered_carrier_at` với `shipping_limit_at` (có xử lý dòng dữ liệu trùng/mâu thuẫn theo cửa sổ hợp lý) và `delivered_customer_at` với `estimated_delivery_at` để phân biệt `late_delivery_seller` vs `late_delivery_logistics`.
  - Ghi `notes/mcp-tools.md` — đối chiếu field thật của `get_order`, `get_order_items`, `get_sellers`, `get_shipment_summary` qua nhiều lần gọi MCP.
- **Điều học được / Đóng góp chính:**
  - Dữ liệu MCP có thể chứa dòng trùng lặp mâu thuẫn (nhiều `shipping_limit_date` cho cùng 1 item) — phải chọn dòng hợp lý theo cửa sổ thời gian [ngày mua, ngày mở case] thay vì tin mù quáng vào dòng đầu tiên.

### ## Hoàng Thái Đạt - 02959
- **Vai trò:** Phụ trách Payment/Refund & Policy agent.
- **Trạng thái:** ✅ Hoàn thành, merged ([PR #7](https://github.com/tsun165/K4-L3A-MultiAgent-MCP-A2A/pull/7), fix tiếp theo qua các commit trực tiếp lên `main` sau khi verify dữ liệu thật).
- **Công việc chi tiết đã hoàn thành:**
  - Triển khai `payment_agent.py` — đối soát `get_order_payments`/`get_refund_timeline`, phát hiện `duplicate_charge` (dòng trùng chính xác), `payment_mismatch` (lệch tổng tiền), `valid_split_payment`, `refund_pending`/`refund_failed`.
  - Triển khai `policy_agent.py` — fetch `get_policy` làm bằng chứng, để coordinator (không phải specialist) quyết định `case_status`/`resolution_actions` dựa trên `primary_issue` đã verify.
  - Sửa bug quan trọng phát hiện qua chạy thật: `get_refund_timeline` trả về object `{events: [...]}` chứ không phải list refund; dữ liệu nhiễu trùng dòng ở `payments`/`items` làm sai tổng tiền `valid_split_payment`; mỗi rule chỉ được xét đúng topic khách hàng claim (tránh dữ liệu nền không liên quan "cướp" kết luận).
- **Điều học được / Đóng góp chính:**
  - `get_policy` là nguồn tham khảo quan trọng cho công thức nghiệp vụ (vd. `refund_pending` nên báo 0 đồng, không phải số tiền đang chờ) — đối chiếu với nó giúp sửa đúng hướng thay vì đoán.

### ## Nguyễn Nguyên Phong - 02691
- **Vai trò:** Phụ trách Verifier & Calibration.
- **Trạng thái:** ✅ Hoàn thành, merged ([PR #5](https://github.com/tsun165/K4-L3A-MultiAgent-MCP-A2A/pull/5), tinh chỉnh qua các commit sau).
- **Công việc chi tiết đã hoàn thành:**
  - Triển khai `agents/verifier.py` — kiểm định đầy đủ invariant trước khi chốt output: schema, quyền sở hữu evidence_ref, tổng tiền hoàn, tính nhất quán status↔refund↔action, seller party_id khớp entity.
  - Triển khai `calibration.py` — hàm tính `confidence` xác định (deterministic), không dùng random, phản ánh mức độ chắc chắn theo rule.
  - Xây dựng bộ test (`tests/test_verifier.py`, `tests/test_workflow_scenarios.py`) — 4+ kịch bản end-to-end với fake gateway, khoá lại các bug đã từng gặp (schema refund, dữ liệu nhiễu, permission fail-closed).
- **Điều học được / Đóng góp chính:**
  - Verifier là lớp phòng thủ cuối cùng: dù specialist có sai sót, verifier phải đảm bảo output không bao giờ vi phạm hợp đồng công khai (schema, evidence provenance) — safe-downgrade về `insufficient_evidence` an toàn hơn là cố "vá" dữ liệu sai.
