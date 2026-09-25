# MCP tools — ghi chú cho Order / Shipment agent

> ## ✅ Trạng thái verify (cập nhật 2026-09-25, sau khi sửa `.env`)
>
> - **Nguyên nhân lỗi `Error executing tool` kéo dài trước đó: `.env` chứa key sai 1 ký tự**
>   (`0285` số 0 thay vì `O285` chữ O, do đọc nhầm từ ảnh chụp màn hình). Trang workspace xác
>   nhận `"detail":"Team access token không hợp lệ."` cho key sai; sau khi user gửi lại key
>   đúng bằng text, mọi tool gọi được bình thường. **Không phải lỗi hệ thống, không phải bug
>   code** — bài học: luôn lấy secret bằng copy-paste text, không OCR từ ảnh.
> - **Toàn bộ §1–§3 bên dưới đã đối chiếu với response JSON thật** (không còn là suy đoán).
>   Field trong §2/§3 khớp gần như tuyệt đối với nội dung đã ghi trước đó (kể cả 2 dòng
>   `shipping_limit_date` mâu thuẫn của case 001) — chứng tỏ lần soạn đầu tiên **là** dữ liệu
>   thật, chỉ là không tái hiện lại được do key sai vào đúng lúc verify.
> - Bug `mcp_gateway.py` dùng `result.isError` (SDK thật dùng `is_error`) đã được vá trên
>   `main` (không liên quan tới lỗi key ở trên, nhưng cũng là một lỗi thật cần vá).
> - **Phát hiện thêm sau khi có dữ liệu thật** (xem `ARCHITECTURE.md` §5):
>   1. `get_refund_timeline` trả về **một object `{order_id, events: [...]}`**, không phải
>      list các refund như bản nháp ban đầu giả định (`refund_status`/`refund_amount`/
>      `refund_id` **không tồn tại**; field thật là `events[].status` và `events[].amount_brl`).
>      Tool này còn báo lỗi thẳng khi order chưa từng có refund (không trả `{events: []}`).
>   2. `payment-agent` từng kiểm tra refund_failed/pending không điều kiện theo topic được
>      claim, khiến 1 case chỉ claim `valid_split_payment` (case 005) bị nhận nhầm thành
>      `refund_failed` vì có sẵn 1 event refund "failed" không liên quan trong dữ liệu nền.
>      Đã sửa: mỗi rule chỉ xét khi đúng topic đó được claim.

Nguồn: `list_tools` + gọi thử trên `L3A_CASE_001` (canceled_order_paid) và `L3A_CASE_003` (late_delivery_seller), policy `EC_POLICY_V1`.

## 1. Danh sách tool

Mọi tool đều bắt buộc có `case_id`. Response đều theo envelope `{schema_version, evidence_ref, result_hash, domain, data, warnings}`.

| Tool | Tham số | domain | Owner gợi ý |
|---|---|---|---|
| `get_order` | order_id | order | order-agent |
| `get_order_items` | order_id | item | order-agent |
| `get_sellers` | order_id | seller | order-agent |
| `get_product_context` | order_id | product | order-agent (hiếm khi cần) |
| `get_shipment_summary` | order_id | shipment | shipment-agent |
| `get_order_payments` | order_id | payment | payment-agent |
| `get_payment_timeline` | order_id | payment | payment-agent |
| `get_refund_timeline` | order_id | refund | payment-agent |
| `get_policy` | policy_version | policy | policy-agent |
| `get_customer_history` | customer_unique_id | customer | chưa dùng |

> ⚠️ `src/student_agent/mcp_gateway.py:27` dùng `result.isError`, nhưng bản `mcp` v2 đang cài đổi tên thành `result.is_error` → `gateway.call()` crash ở mọi call (server vẫn ghi audit). Tương tự `structuredContent` → `structured_content`. Cần fix trước khi tích hợp.

## 2. Field quan trọng

### `get_order` → `data` là object
| Field | Ghi chú |
|---|---|
| `order_id` | so với `claimed_order_id` trong input |
| `customer_id` | |
| `order_status` | `canceled`, `delivered`, (kỳ vọng `unavailable`, …) — **nguồn chính để xác định trạng thái** |
| `order_purchase_timestamp`, `order_approved_at` | |
| `order_delivered_carrier_date` | ngày seller bàn giao cho carrier |
| `order_delivered_customer_date` | `null` nếu chưa giao |
| `order_estimated_delivery_date` | hạn giao dự kiến cho khách |

### `get_order_items` → `data` là list
`order_id`, `order_item_id`, `product_id`, `seller_id`, `shipping_limit_date`, `price` (string, vd `"79.00"`), `freight_value` (string).

### `get_sellers` → `data` là list
`seller_id`, `seller_zip_code_prefix`, `seller_city`, `seller_state`.

### `get_product_context` → `data` là list
`order_item_id`, `product_id`, `seller_id`, `product.product_category_name`, `category_name_english`. Không ảnh hưởng tới 4 issue, **không cần gọi** (thêm evidence thừa sẽ làm giảm evidence precision).

### `get_shipment_summary` → `data` là object
| Field | Ghi chú |
|---|---|
| `order_status` | trùng với `get_order` |
| `delivered_carrier_at` | = `order_delivered_carrier_date` |
| `delivered_customer_at` | = `order_delivered_customer_date` |
| `estimated_delivery_at` | = `order_estimated_delivery_date` |
| `shipping_limits[]` | `{order_item_id, seller_id, shipping_limit_at}` — hạn bàn giao carrier của seller |
| `events[]` | `{order_id, event_at, event_type, actor, status}`, vd `delivered_late / seller / confirmed` |

→ **Shipment agent chỉ cần 1 call `get_shipment_summary`** là có đủ timestamps + shipping limit + seller_id.

### Không có shipment id
Chưa thấy field shipment id nào trong mọi response → `shipment_ids` để `[]` trừ khi evidence có ID thật. Không được tự tạo.

## 3. Bẫy dữ liệu đã thấy

1. **Item bị trùng và mâu thuẫn**: cả 2 case đều có 2 dòng item cùng `order_item_id` nhưng khác `shipping_limit_date` và `freight_value`.
   - Case 001: limit `2017-12-23` / freight 10 và limit `2018-05-14` / freight 18
   - Case 003: limit `2018-02-22` / freight 18 và limit `2018-03-12` / freight 10
   - Case 004: limit `2018-03-26` và `2018-02-11`, trong đó dòng nhiễu có ngày **trước** ngày mua. Nếu chọn "hạn sớm nhất" thì sẽ kết luận sai là seller trễ.
   - Rule đang dùng (`select_shipping_limits`): chỉ nhận limit nằm trong khoảng **[order_purchase_timestamp, opened_at]**; nếu không có dòng nào thỏa thì lấy hạn sớm nhất. Đã kiểm với dữ liệu thật của case 003 và 004.
   - Chưa đưa vào `data_conflicts` của output (coordinator đang để `[]`); id các item bị mâu thuẫn nằm ở `findings["shipping_limit_conflicts"]`.
   - `item_ids` phải khử trùng lặp (unique).
2. **Event gây nhiễu**: case 001 có `order_status=canceled`, chưa giao cho khách, nhưng lại có event `delivered_late / actor=seller` vào `2018-05-25` (sau `opened_at` 2018-01-01). → `events` chỉ là tín hiệu phụ; **`order_status` + timestamps mới là nguồn quyết định**.
3. Order bị hủy vẫn có `delivered_carrier_date` → không suy ra "đã giao" từ ngày carrier.
4. Tiền trả về dạng **string** → phải `Decimal(...)`/`float(...)` trước khi tính.

## 4. Policy `EC_POLICY_V1` (phần liên quan)

| Issue | case_status | recommended_action | responsible (party_type) | refund_brl ví dụ |
|---|---|---|---|---|
| canceled_order_paid | action_required | `issue_refund` | platform, party_id `null` | 79.0 |
| unavailable_order_paid | action_required | `issue_refund` | seller, party_id = seller_id | 89.0 |
| late_delivery_seller | action_required | `refund_freight` | seller, party_id = seller_id | 18.0 |
| late_delivery_logistics | action_required | `refund_freight` | logistics_provider, party_id `null` | 16.0 |
| unsupported_claim | no_action | `document_no_action` | customer | 0 |

`refund_brl` và `party_id` trong policy là **ví dụ** (seller id không thuộc case đang xét), nên phải tính lại từ evidence của case hiện tại.
Late delivery → hoàn freight của item. Cancel/unavailable → hoàn số tiền đã trả; lấy từ payment agent (@Liber72).

## 5. Rule nháp cho 4 issue

Chung: `order_id = claimed_order_id`. Nếu `get_order` lỗi / not found / `order_id` không khớp → không kết luận, trả về `insufficient_evidence` cho coordinator.

### Order agent (`get_order`, `get_order_items`, `get_sellers`)
| Issue | Điều kiện | cause_code | responsible |
|---|---|---|---|
| `canceled_order_paid` | `order_status == "canceled"` **và** payment agent xác nhận đã trả > đã hoàn | `ORDER_CANCELED_AFTER_PAYMENT` | `platform` / `null` |
| `unavailable_order_paid` | `order_status == "unavailable"` **và** đã trả > đã hoàn | `ORDER_UNAVAILABLE_AFTER_PAYMENT` | `seller` / seller_id |

- refund = paid − already_refunded (từ payment agent), `reason_code` = `CANCELED_ORDER_REFUND` / `UNAVAILABLE_ORDER_REFUND`, `entity_id` = order_id.
- Nếu đã hoàn đủ → không còn là issue này (có thể coordinator chuyển sang `refund_pending`/`no_action`).
- Evidence cần: `get_order` (status) + payment evidence (của @Liber72). Thêm `get_order_items`/`get_sellers` nếu cần seller_id (unavailable).

### Shipment agent (`get_shipment_summary`, `get_order_items`, `get_order`)
Ký hiệu: `limit` = shipping_limit_at đã chọn theo mục 3.1, `carrier` = delivered_carrier_at, `customer` = delivered_customer_at, `eta` = estimated_delivery_at.

| Issue | Điều kiện | cause_code | responsible |
|---|---|---|---|
| `late_delivery_seller` | `carrier > limit` | `SELLER_LATE_HANDOVER` | `seller` / seller_id của item trễ |
| `late_delivery_logistics` | `carrier <= limit` **và** `customer > eta` | `CARRIER_LATE_DELIVERY` | `logistics_provider` / `null` |
| (không trễ) | `carrier <= limit` và (`customer <= eta` hoặc chưa giao mà chưa quá eta) | — | `findings["claim_check"]` = `CLAIM_CONTRADICTED`; coordinator quyết định `unsupported_claim` |

- Seller trễ **và** khách nhận trễ → ưu tiên `late_delivery_seller`.
- refund = `freight_value` của item trễ (dòng item được chọn), `reason_code` = `LATE_DELIVERY_COMPENSATION` (policy: `refund_freight`), `entity_id` = order_item_id.
- `order_status` phải là `delivered`/có `delivered_customer_at`; nếu `canceled` thì không kết luận late delivery (bẫy của case 001).
- Kiểm tra với case 003: carrier 02-26 > limit 02-22 → `late_delivery_seller`, seller `seller-71303d7e93b3`, freight 18 ✔ khớp claim.

### Entities (chỉ lấy ID có trong evidence)
- `order_ids`: `data.order_id` của `get_order`
- `item_ids`: `order_item_id` (unique)
- `seller_ids`: `seller_id` từ items/shipping_limits/sellers
- `shipment_ids`: `[]` (không có trong evidence)

### Trace
Mỗi response được dùng → `tool_result_consumed`, `actor="order-agent"`/`"shipment-agent"`, `tool_name`, `evidence_refs=[ref]`.

## 6. Kết quả chạy thử thật (solve_case, 1 case/lần)
| Case | Claim | Kết quả |
|---|---|---|
| 002 | unavailable_order_paid | `unavailable_order_paid`, seller chịu trách nhiệm (xác nhận `order_status == "unavailable"`) |
| 003 | late_delivery_seller | `late_delivery_seller`, freight 18 |
| 004 | late_delivery_logistics | `late_delivery_logistics`, freight 18 (sau khi sửa rule chọn limit) |

## 7. Payment / Policy — field thật (verify 2026-09-25)

### `get_order_payments` → `data` là list
`order_id`, `payment_sequential`, `payment_type` (`credit_card`/`voucher`), `payment_installments`, `payment_value`. **Số ở dạng string** (`"79.00"`), `payment_sequential`/`payment_installments` cũng là string (`"1"`).

Ví dụ case duplicate_charge (007): 4 dòng, 2 cặp `(credit_card, 64.00, seq1)` và `(voucher, 64.00, seq2)` mỗi cặp lặp lại đúng 2 lần → nhận diện trùng bằng key `(payment_type, amount, installments)`, dòng lặp thứ 2 là bản trùng.

### `get_refund_timeline` → `data` là **OBJECT**, không phải list
```json
{"order_id": "...", "events": [
  {"order_id": "...", "event_at": "...", "event_type": "refund_requested", "amount_brl": "89.00", "status": "pending"}
]}
```
- `status` quan sát được: `pending`, `failed` (chưa thấy `completed` trong mẫu đã gọi).
- **Không có `refund_id`.** Dùng `order_id` làm `entity_id` của refund line.
- **Tool báo lỗi thẳng** (`Error executing tool get_refund_timeline`) khi order chưa từng có refund nào — không trả `{"events": []}`. Phải bọc try/except riêng, coi lỗi này là "không có refund", không phải crash.
- ⚠️ **Order có thể có event refund không liên quan tới topic đang claim** (case 005 chỉ claim `valid_split_payment` nhưng vẫn có 1 event `status=failed`). Payment-agent chỉ được kết luận `refund_failed`/`refund_pending` khi **đúng topic đó được claim**, không được suy diễn từ event có sẵn bất kể topic.

### `get_policy` → `data.rules[<primary_issue>]`
Trả về **toàn bộ 10 rule** (không phải riêng theo case), mỗi rule có `case_status`, `recommended_action`, `refund_brl` (giá trị **ví dụ**, không phải số của case đang xét), `responsible_parties`. Dùng để tham khảo action/case_status mặc định, KHÔNG dùng `refund_brl` mẫu này làm số tiền thật.

## 8. Còn mở
- Theo STANDARDS: tên tool chỉ lấy từ `agents/tools.py`, mã chuẩn lấy từ `agents/vocab.py`. Specialist không đề xuất `unsupported_claim` mà ghi `findings["claim_check"][topic]` = `CLAIM_SUPPORTED`/`CLAIM_CONTRADICTED` để coordinator đọc.
- `total_refunded_brl` (order-agent dùng để trừ vào refund canceled/unavailable) chỉ cộng event có `status == "completed"` — **chưa từng quan sát được giá trị này thật**, cần theo dõi ở lần chạy full tiếp theo xem có case nào order đã được hoàn đủ chưa mà vẫn bị đề xuất refund thêm không.
