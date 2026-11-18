# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (backend REPLAY: verdict của judge nằm sẵn trong CSV, n = 100 mỗi tiêu chí, lớp dương = `FAIL`).

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **PASS** (exit 0) | **dùng**, nhưng chỉ non-blocking, như tín hiệu phụ cho giọng văn |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **FAIL** (exit 1): κ và R dưới sàn | **loại**, không được dùng làm gate |

Ma trận nhầm lẫn:

| | tp | fp | fn | tn | Tỉ lệ FAIL quan sát → hiệu chỉnh Rogan-Gladen |
|---|---|---|---|---|---|
| tone_ok | 24 | 4 | 6 | 66 | 0.28 → 0.30 |
| helpful | 9 | 0 | 21 | 70 | 0.09 → 0.30 |

## ≥5 case audit thủ công

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha…" | PASS | FAIL | **Người** | Xưng "em" và dùng "nha" là quá suồng sã với khách khiếu nại. Rubric thêm dòng: *xưng hô bắt buộc "LogiX – anh/chị"; "em/bạn/nha/nhé" thì FAIL*, kèm 2 ví dụ few-shot. |
| 2 | H-029 "Dạ, vui lòng chờ… không cần nhắn thêm." | PASS | FAIL | **Người** | Judge chỉ bám vào từ lịch sự ở đầu câu ("Dạ, vui lòng"). Rubric thêm: *chấm cả câu, câu ra lệnh hoặc chặn khách liên hệ thì FAIL dù có "Dạ"*. |
| 3 | H-051 "LogiX đã refund 90.000đ…" | FAIL | PASS | **Người** | Hướng dẫn CS của LogiX cho phép dùng từ "refund". Rubric thêm whitelist thuật ngữ tiếng Anh (refund, voucher, ship). |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ…" | FAIL | PASS | **Người** | Judge phạt vì "thiếu xin lỗi". Rubric ghi rõ: *không bắt buộc có lời xin lỗi; ngắn mà đủ ý vẫn PASS*. |
| 5 | H-081 "đơn O-107 chưa đủ điều kiện hoàn…" | FAIL | PASS | **Judge** | Người gán nhãn đọc nhầm ticket. **Sửa nhãn người thành FAIL** và ghi log thay đổi nhãn (không sửa verdict judge). |
| 6 | H-063 "…và sẽ hoàn thêm cho anh/chị" | PASS | FAIL | **Người** | Đây là hứa ngoài kết quả hệ thống, một lỗi nội dung chứ không phải giọng văn. Chuyển sang grader code (reply ↔ ledger/status); tone judge không nên gánh việc này. |
| 7 | H-070 "…ạ 😂😂" | PASS | FAIL | **Người** | Rubric thêm: *emoji cười trong ngữ cảnh khiếu nại thì FAIL*. |
| 8 | H-088 "Dạ, dạ, LogiX đã hoàn…" | FAIL | PASS | **Người** (lỗi gõ nhỏ) | Lỗi lặp từ không làm hỏng giọng văn. Rubric ghi: *lỗi chính tả hoặc lặp từ đơn lẻ không tính*. |
| 9 | HL-015 / HL-020 / HL-100 (helpful): "đã hoàn 55.000đ" nhưng ledger 40.000đ | PASS | FAIL | **Người** | Judge **không nhìn thấy ledger**, nên không thể biết số tiền sai. 18/21 FN của helpful là "sai tiền". Hướng sửa: không dùng judge cho việc này mà dùng grader code `reply_amount_matches_ledger` (FM-08). |
| 10 | HL-016 / HL-043 (helpful): xin lỗi dài, số tiền sai | PASS | FAIL | **Người** | Judge bị câu văn dài và lời xin lỗi đánh lừa (verbosity bias). Rubric helpful phải đặt *đúng số tiền / đúng trạng thái* lên trước *lịch sự*. |
| 11 | HL-047 / HL-091 / HL-097 "Ok bạn, đơn … xử lý rồi nhé." | PASS | FAIL | **Người** | Sai xưng hô và không nói kết quả cụ thể. Rubric helpful thêm: *reply phải nêu số tiền hoặc trạng thái cụ thể*. |

## Kết luận

- **tone_ok: dùng** (κ 0.757 ≥ 0.6, P 0.857 ≥ 0.8, R 0.8 ≥ 0.7), nhưng chỉ ở mức non-blocking. Lỗi chủ yếu là FN về xưng hô (6 FN), sửa bằng rubric và few-shot ở mục 1, 2, 7. Tỉ lệ FAIL thật ước lượng bằng Rogan-Gladen là 0.30, so với 0.28 judge quan sát.
- **helpful: loại khỏi gate.** Recall 0.30 nghĩa là judge bỏ sót 21/30 reply tệ, và κ = 0.375 dưới sàn 0.6. Precision = 1.0 trông đẹp nhưng chỉ vì judge gần như luôn nói PASS (9 FAIL trên 100). Nếu dùng judge này làm gate, PR làm sai số tiền vẫn sẽ được merge. Nguyên nhân gốc: **18/21 FN là sai tiền**, mà judge không có ledger trong ngữ cảnh. Tiền phải chấm bằng code, theo đúng quy tắc của taxonomy, nên việc này chuyển sang metric `reply_amount_matches_ledger`. Judge helpful chỉ được xét lại sau khi rubric mới (mục 9 đến 11) đạt sàn trên một bộ nhãn người **mới**.
- Rogan-Gladen trên helpful hiệu chỉnh 0.09 lên 0.30, đúng bằng tỉ lệ FAIL của nhãn người. Điều này cho thấy con số "chỉ 9% reply tệ" do judge báo là sai lệch nặng.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai. Rubric sửa xong cần nhãn người mới để có tập held-out thật. Một nhãn người (H-081) đã sai, nên cần 2 người gán nhãn độc lập và đo κ giữa hai người.
