# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

## Case: `seeded_regression C-002@11`

Output của lệnh trên (rút gọn):

```
== seeded_regression C-002@11  lab8 grader: FAIL (status resolved != denied)
   1 handoff            {... "allowed_tools": ["lookup_order", "lookup_policy", ...]}
   2 investigator       {"lookup_order": {"visible": true, "order": {"order_id": "O-202", "status": "delivered", "delay_days": 2, ...
   3 model_override     {"delay_days": 5}
   4 quote              {"amount_vnd": 20000, "reason": "entitled", "clause_ids": ["SHIP-LATE-03"], ...}
   5 refund_history     {"refund_count": 0, "refunded_vnd": 0}
   6 reply              {"text": "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202."}
  refund_vnd     ledger=20000    label=0   <-- differs
  refund_count   ledger=1        label=0   <-- differs
  status         result=resolved label=denied
```

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, trong report run ID `E-seeded_regression-20261117-0825` (eval set `08253117051d`, item E-002, fail ở cả 5 seed) |
| Triệu chứng | `pass_rate` FAIL (status resolved ≠ denied), `refund_amount_correct` FAIL (ledger 20.000đ ≠ nhãn 0), `db_fact_kept` FAIL. Reply "đã hoàn 20.000đ" **khớp ledger**, nên `reply_amount_matches_ledger` PASS: reply nghe trôi chảy và "đúng" nhưng khoản hoàn thì sai. |
| Bước sai đầu tiên | **Event #3 `model_override`** `{"delay_days": 5}`: agent ghi đè `delay_days` của DB bằng con số khách tự khai trong ticket ("giao trễ 5 ngày"). |
| Vì sao là bước *đầu tiên* | #1 `handoff` đúng hợp đồng (chỉ tool đọc). #2 `investigator` đọc đúng DB: O-202 `delivered`, `delay_days: 2`; với 2 ngày trễ thì không có điều khoản nào áp dụng. Từ #3 trở đi mọi bước đều dựa trên số 5 sai: #4 quote 20.000đ theo SHIP-LATE-03, ledger ghi 20.000đ, #6 reply báo resolved. |
| Failure mode | **FM-03 ticket_trust** (layer model/prompt). Lỗi này cũng lộ trên baseline `swarm` ở C-002@55 (seed % 5 == 0). |
| Đối chứng | `fixed C-002@11`: #2 giống hệt, **không có `model_override`**, #3 quote `amount_vnd: 0, reason: no_entitlement`, #4 reply "chưa đủ điều kiện". Ledger 0, status denied, lab8 PASS. |
| Regression test | `tests/test_lab9_trace_case.py`: `test_lab9_trace_case_c002_first_wrong_step_is_override` khẳng định event trước `model_override` là `[handoff, investigator]`, payload là `{"delay_days": 5}`, `DbFactKept` = False, `RefundAmountCorrect` = False. Test này fail khi lỗi xuất hiện, tức là bắt được nó. `test_lab9_trace_case_c002_fixed_keeps_db_fact` khẳng định trên `fixed` không có override và cả hai grader PASS. **Đã chạy: 2/2 pass** (thuộc nhóm `-m student -k lab9`, chạy trong job `eval-gate`). |
| Chưa chắc chắn | Trace không cho biết *vì sao* model chọn tin ticket: MockLLM gài cứng bằng công tắc `ticket_trust`. Với LLM thật, nguyên nhân có thể nằm ở prompt, ở thứ tự nguồn trong ngữ cảnh, hoặc ở nhiệt độ. Trace cũng không ghi event `issue_refund` của host, nên thời điểm ghi ledger chỉ suy ra được từ snapshot. Biến thể `quiet_override` (cùng lỗi nhưng không để lại event) sẽ qua mặt `db_fact_kept`; khi đó chỉ còn `refund_amount_correct` và `pass_rate` bắt được. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
