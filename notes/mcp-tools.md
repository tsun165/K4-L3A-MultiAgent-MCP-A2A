# MCP Tools — Ghi chú field quan trọng

> Tài liệu này ghi lại các field quan trọng từ mỗi MCP tool domain.
> Danh sách tool chính thức lấy từ `day09 mcp-tools` (25/09/2026).

## 10 Tools chính thức (từ MCP server)

| # | Tool Name              | Domain    | Mô tả                                              |
|---|------------------------|-----------|-----------------------------------------------------|
| 1 | `get_order`            | order     | Thông tin đơn hàng: trạng thái, ngày tạo, timeline  |
| 2 | `get_order_items`      | item      | Chi tiết items: giá, freight, seller_id, product_id  |
| 3 | `get_order_payments`   | payment   | Thanh toán: loại, số lần trả, giá trị, installments |
| 4 | `get_payment_timeline` | payment   | Lịch sử thanh toán theo timeline                     |
| 5 | `get_refund_timeline`  | refund    | Hoàn tiền: trạng thái, số tiền, ngày xử lý          |
| 6 | `get_policy`           | policy    | Chính sách: điều kiện hoàn tiền, thời hạn            |
| 7 | `get_shipment_summary` | shipment  | Vận chuyển: ngày gửi/nhận, carrier, trạng thái      |
| 8 | `get_sellers`          | seller    | Người bán: ID, thành phố, state                     |
| 9 | `get_customer_history` | customer  | Khách hàng: lịch sử mua hàng, ID                    |
|10 | `get_product_context`  | product   | Sản phẩm: tên danh mục, kích thước, cân nặng        |

## Evidence Response Envelope

Mỗi MCP call trả về:
```json
{
  "schema_version": "day09-mcp-evidence-v1",
  "evidence_ref": "ev_<token>",       // KHÔNG tự tạo, dùng đúng cái này
  "result_hash": "sha256:<hex64>",
  "domain": "<domain>",
  "data": { ... },                     // Dữ liệu thực tế
  "warnings": ["..."]                  // Cảnh báo (tùy chọn)
}
```

## Field quan trọng theo tool

### `get_order`
- `order_id`: ID đơn hàng (match `claimed_order_id` trong input)
- `order_status`: `delivered`, `shipped`, `canceled`, `unavailable`, `created`, `approved`, `processing`, `invoiced`
- `order_purchase_timestamp`: thời điểm mua
- `order_approved_at`: thời điểm duyệt
- `order_delivered_carrier_date`: ngày carrier giao
- `order_delivered_customer_date`: ngày khách nhận
- `order_estimated_delivery_date`: ngày giao dự kiến

### `get_order_items`
- `order_item_id`: ID item
- `product_id`: ID sản phẩm
- `seller_id`: ID người bán
- `price`: giá sản phẩm (BRL)
- `freight_value`: phí vận chuyển (BRL)
- Lưu ý: `price + freight_value` = tổng khách phải trả cho item đó

### `get_order_payments`
- `payment_type`: `credit_card`, `boleto`, `voucher`, `debit_card`
- `payment_sequential`: thứ tự thanh toán (1, 2, 3...)
- `payment_installments`: số kỳ trả góp
- `payment_value`: **số tiền thực trả** (BRL) — dùng `Decimal` để tính toán
- Lưu ý: một đơn hàng có thể có **nhiều payment** (split payment)

### `get_payment_timeline`
- Lịch sử chi tiết các sự kiện thanh toán theo thời gian
- Có thể bao gồm: ngày tạo payment, ngày xác nhận, ngày hoàn thành
- Hữu ích để phát hiện `duplicate_charge` (cùng thời điểm, cùng số tiền)

### `get_refund_timeline`
- `refund_status`: `pending`, `completed`, `failed`
- `refund_amount`: số tiền hoàn (BRL)
- `refund_date`: ngày xử lý hoàn
- Timeline các sự kiện hoàn tiền
- Dùng để xác định `refund_pending` / `refund_failed`

### `get_policy`
- `policy_version`: phiên bản chính sách (e.g. `EC_POLICY_V1`)
- Quy tắc hoàn tiền, thời hạn, điều kiện áp dụng
- Có thể trả `rules[]` chứa decision per topic
- Đọc qua MCP tool, emit `policy_decided` sau khi phân tích

### `get_shipment_summary`
- `shipping_limit_date`: hạn cuối gửi hàng
- Carrier, ngày giao thực tế vs dự kiến
- Dùng xác định `late_delivery_seller` vs `late_delivery_logistics`

### `get_sellers`
- `seller_id`: ID người bán
- `seller_city`, `seller_state`: địa chỉ
- Dùng để xác định `responsible_parties` khi issue liên quan seller

### `get_customer_history`
- Lịch sử khách hàng: các đơn hàng trước đó
- Hữu ích cho context nhưng **không phải ground truth**

### `get_product_context`
- Tên danh mục, kích thước, cân nặng sản phẩm
- Có thể hữu ích khi đánh giá freight value

## Agent → Tool mapping

| Agent          | Tools sử dụng                                            |
|----------------|----------------------------------------------------------|
| Payment Agent  | `get_order_payments`, `get_order_items`, `get_refund_timeline` |
| Policy Agent   | `get_policy`                                              |
| Order Agent    | `get_order`, `get_order_items`                            |
| Shipment Agent | `get_shipment_summary`, `get_order`                       |
| Coordinator    | Không gọi tool trực tiếp, nhận kết quả từ agents          |

## Action Codes (cho `resolution_actions`)

| Issue Type               | Action Code                                      | Case Status           |
|--------------------------|--------------------------------------------------|-----------------------|
| `valid_split_payment`    | `no_action`                                      | `no_action`           |
| `payment_mismatch`       | `refund_overpayment`                             | `action_required`     |
| `duplicate_charge`       | `refund_duplicate_charge`                        | `action_required`     |
| `refund_pending`         | `escalate_pending_refund`                        | `needs_investigation` |
| `refund_failed`          | `retry_refund` / `escalate_refund_failure`       | `action_required`     |
| `canceled_order_paid`    | `refund_canceled_order`                          | `action_required`     |
| `unavailable_order_paid` | `refund_unavailable_order`                       | `action_required`     |
| `late_delivery_seller`   | `contact_seller_late_shipment`                   | `action_required`     |
| `late_delivery_logistics`| `escalate_logistics_delay`                       | `action_required`     |

## Quy tắc quan trọng

1. **Luôn truyền đúng `case_id`** khi gọi MCP tool
2. **Không tự tạo `evidence_ref`** — dùng đúng giá trị trả về
3. **Dùng `Decimal`** cho mọi tính toán tiền tệ, làm tròn 2 chữ số
4. **`recommended_refund_brl == sum(refund_lines)`** — tổng phải khớp
5. **`resolution_actions` unique**, khớp với `case_status`
6. **Evidence không được dùng chéo case**
7. **Gọi tool có chừng mực** — mọi call bị audit
