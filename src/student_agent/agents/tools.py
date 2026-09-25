"""MCP tool registry. Every name here must appear in notes/mcp-tools.md."""

from __future__ import annotations

GET_ORDER = "get_order"
GET_ORDER_ITEMS = "get_order_items"
GET_SELLERS = "get_sellers"
GET_PRODUCT_CONTEXT = "get_product_context"
GET_SHIPMENT_SUMMARY = "get_shipment_summary"
GET_ORDER_PAYMENTS = "get_order_payments"
GET_PAYMENT_TIMELINE = "get_payment_timeline"
GET_REFUND_TIMELINE = "get_refund_timeline"
GET_POLICY = "get_policy"
GET_CUSTOMER_HISTORY = "get_customer_history"

ORDER_TOOLS: frozenset[str] = frozenset({GET_ORDER, GET_ORDER_ITEMS, GET_SELLERS})
# Shipment needs item freight (refund) and the purchase date (to discard noisy limit rows).
SHIPMENT_TOOLS: frozenset[str] = frozenset({GET_SHIPMENT_SUMMARY, GET_ORDER_ITEMS, GET_ORDER})
# Payment needs item price+freight (expected_total) to detect payment_mismatch.
PAYMENT_TOOLS: frozenset[str] = frozenset(
    {GET_ORDER_PAYMENTS, GET_PAYMENT_TIMELINE, GET_REFUND_TIMELINE, GET_ORDER_ITEMS}
)
POLICY_TOOLS: frozenset[str] = frozenset({GET_POLICY})

ALL_TOOLS: frozenset[str] = frozenset(
    {
        GET_ORDER,
        GET_ORDER_ITEMS,
        GET_SELLERS,
        GET_PRODUCT_CONTEXT,
        GET_SHIPMENT_SUMMARY,
        GET_ORDER_PAYMENTS,
        GET_PAYMENT_TIMELINE,
        GET_REFUND_TIMELINE,
        GET_POLICY,
        GET_CUSTOMER_HISTORY,
    }
)
