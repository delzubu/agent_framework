---
title: Structured output — result_field example
result_field: parameters
case_run_mode: no_callbacks
---
Extract the following details from this text and return them as structured data.
Text: "Order #4821 placed by John Smith on 2024-03-15 for $149.99"
Return: order_number, customer_name, order_date, amount
---
- The result must contain an order_number field with value 4821
- The result must contain a customer_name field with value "John Smith"
- The result must contain an order_date field with value "2024-03-15"
- The result must contain an amount field with value 149.99 or "149.99"
- All four fields must be present in the structured output
- The response must not include extra commentary outside the structured data
