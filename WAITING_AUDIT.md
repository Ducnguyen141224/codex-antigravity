# Kiểm tra cơ chế chờ và token

Đo ngày 14/11/2026 trên Windows, Codex App Server 0.150.1, model Codex `gpt-5.6-luna`, AGY 1.5.8 và model AGY `gemini-3.8-flash`.

Các task dùng AGY thật để tạo `hello0.py`, sau đó Supervisor chạy kiểm tra thật với một khoảng giữ 90 giây để vượt timeout mặc định. Codex đọc Git diff, chạy assertion độc lập và gửi quyết định nghiệm thu. Kế hoạch đã được chuẩn bị trước cho phép đo, nên số token dưới đây đo việc tiếp tục giám sát và review. Bài đo không bao gồm token lập kế hoạch từ đầu, token của chat điều tra này hoặc token AGY.

## Kết quả

| Tiêu chí | Kết quả và bằng chứng |
| --- | --- |
| Codex yên trong lúc chờ pipeline | PASS trong ca cuối. Một lời gọi MCP giữ 84,17 giây, 0 đợt reasoning và 0 cập nhật usage trong khoảng lời gọi đang chờ. |
| Python giám sát độc lập | PASS. ACP callback đánh thức `asyncio.Event`. Supervisor chạy ở tiến trình riêng và không có client LLM cho lập kế hoạch/review. |
| Tiếp tục review khi hoàn thành | PASS khi lượt Codex còn mở. Codex nhận kết quả MCP rồi đọc diff, chạy assertion và nghiệm thu trong cùng turn. |
| Tự bắt đầu lượt mới sau khi đóng chat | FAIL. Plugin giữ dữ liệu để tiếp tục theo task ID. Chưa triển khai cơ chế khởi tạo lượt chat tự động. |
| Ngắt kết nối không mất task | PASS. Đóng Codex App Server khi MCP còn pending và task là `RUNNING`; task vẫn `RUNNING`, sau đó lấy lại review và nghiệm thu qua kết nối mới. |
| Timeout và mất ACP | PASS. Thử thật trả `PENDING` khi hạn chờ ngắn hết, `TIMED_OUT` khi hard deadline hết, và `FAILED / ACP_CONNECTION_LOST` sau khi dừng AGY. |
| Supervisor crash và phục hồi | PASS. Dừng cưỡng bức khi session AGY đã chạy, khởi động lại thấy `INTERRUPTED`, worktree còn nguyên. Retry được lưu với bộ đếm tăng. |
| Dữ liệu trả về Codex | PASS trong các ca đo. MCP chờ trả trạng thái gọn khoảng 1.699 byte theo JSON ghi nhận. Review lấy metadata và diff. Kết quả loại bỏ event history và transcript AGY. |
| Giao diện Desktop | CHƯA KIỂM CHỨNG trực tiếp. Đã chạy bằng host Codex App Server và plugin cục bộ thật, chưa đóng/mở cửa sổ Desktop để kiểm tra UI. |
| Tiết kiệm token dài hạn | CHƯA KẾT LUẬN. Đã loại bỏ lượt model phát sinh trong lúc chờ ở ca cuối. Tổng token, cache và output biến động giữa các ca. |

## Số liệu Codex thực tế

| Cách chờ | Giây chờ | MCP chờ | Turn Codex | Đợt model ghi nhận | Đợt reasoning trong lúc chờ | Input | Input cache | Output |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CLI qua shell | 96,28 | 0 | 1 | 12 | 3 | 303.926 | 280.960 | 2.461 |
| MCP, code-mode yield mặc định | 83,83 | 1 | 1 | 14 | 2 | 374.510 | 354.048 | 3.200 |
| MCP, tăng thời hạn yield của code-mode | 84,17 | 1 | 1 | 10 | 0 | 267.890 | 236.800 | 3.003 |

Ca cuối có 9 lời gọi lớp công cụ `exec`, gồm 6 lệnh shell, tra cứu công cụ và một lời gọi MCP. Ca CLI có 10 `exec` và 1 `wait`; ca MCP mặc định có 11 `exec` và 2 `wait`. Số MCP trong bảng là lời gọi từ model, không tính API do bộ đo gọi để chuẩn bị task và xử lý quyền.

Input chưa cache của ba ca lần lượt là 22.966, 20.462 và 31.090. Vì input chưa cache của ca cuối tăng, dữ liệu này chưa chứng minh mức giảm token tính phí hoặc chi phí dài hạn. Output đã bao gồm reasoning output theo usage của host.

Ba ca chính báo tổng cộng 954.990 token xử lý, gồm cả input cache. Một ca chẩn đoán bổ sung được hủy trước nghiệm thu ghi 125.121 token. Tổng các phiên Codex đo được là 1.080.111 token xử lý. Các host chỉ dùng để kiểm tra kết nối và các ca lỗi Python không khởi động lượt model.

## Thay đổi đã áp dụng

- CLI giữ một yêu cầu HTTP theo sự kiện tối đa 1800 giây. MCP ngắn vẫn giữ timeout cũ để tương thích.
- Plugin 0.2.0 đóng gói server `agy_wait` chỉ có tool `wait_for_review`, với timeout host 1860 giây.
- Hướng dẫn plugin đặt `yield_time_ms` cho code-mode. Timeout MCP riêng lẻ vẫn để host đánh thức model mỗi 30 giây nếu dùng yield mặc định.
- Nhật ký IPC ghi tên thao tác và thời lượng, phục vụ đếm request mà không ghi payload hoặc credentials.

Phép đo mới xác minh thời gian chờ hơn 80 giây, chủ yếu trong giai đoạn verification 90 giây. Lượt AGY thực thi code ở ca cuối mất khoảng 15 giây trước verification. Chưa có soak test liên tục nhiều giờ, AGY suy luận liên tục hàng chục phút, hoặc phép đo tải nhiều dự án để kết luận về độ ổn định dài hạn. Cấu hình chờ 30 phút cần được tôn trọng bởi host đang sử dụng.

## Bằng chứng trên máy

- `.runtime/wait-audit-61e5f6f2/measurement.json` và `codex-events.jsonl` — ca CLI, task `20ec7187719943bdaa8f900b28931ac6`.
- `.runtime/mcp-wait-audit-38ef7864/measurement.json` — ca MCP yield mặc định, task `f9e432221a9c4095af611754854c77c2`.
- `.runtime/mcp-wait-audit-4e30b20a/measurement.json` — ca tối ưu, task `1e429863ab9943afb478df280ab254ff`, revision nghiệm thu `f8f89c5a88dbcf245fabf1196dd3e1902bc9cb15`.
- `.runtime/recovery-audit-00761738/measurement.json` — crash, retry, timeout và mất ACP thật.
- `.runtime/active-disconnect-audit/measurement.json` — host ngắt khi task còn chạy, revision nghiệm thu sau khi kết nối lại `67952a210e521aa494960b83c7313add2ec3885d`.

Các dữ liệu đo cục bộ nằm trong `.gitignore`. Mã nguồn và plugin mới đã áp dụng trên máy. Thay đổi này chưa push lên GitHub.
