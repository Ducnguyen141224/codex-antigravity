# Kết quả kiểm thử trên Windows

Kiểm thử ngày 14/11/2026 theo giờ Việt Nam.

| Phạm vi | Kết quả |
| --- | --- |
| Fixture ACP, IPC, MCP stdio và kiểm thử logic | 15 pass. Gồm kế hoạch, dependency, đường dẫn Unicode, idempotency, cách ly Git, review gate, diff giữa hai vòng, permission, quota, cancellation, timeout và recovery. |
| AGY thật | 1 pass, 292 giây. Antigravity CLI 1.5.8, ACP protocol 1, Gemini 3.8 Flash, terminal sandbox bật. |
| End-to-end bản đã cài | Pass. Codex App Server → MCP stdio đã đăng ký → Supervisor do Task Scheduler khởi động → AGY thật → verification → review → fix → `ACCEPTED`. |
| Đăng ký Codex MCP | Codex App Server đọc cấu hình thật và gọi `codex_agy/health_check`, nhận `READY` với structured content. |
| Windows launcher | Cài đặt qua Windows PowerShell. Khởi động thủ công chính tác vụ Task Scheduler, xác nhận trạng thái `Running` và IPC chỉ lắng nghe ở `127.0.0.1:18765`. |
| Kiểm tra tĩnh | Ruff lint và format, compileall, parser của Windows PowerShell, schema kế hoạch mẫu đều pass. |
| Node.js | Verification chạy `npm run test` trên thư mục tạm chứa dấu cách và nhận exit code 0. |

## Bằng chứng AGY thật

Task `5d85f51cf6e04d9999434b4175ad7cfe` chạy trong repo tạm có đường dẫn Unicode và dấu cách.

1. AGY tạo `hello0.py`. Supervisor chụp revision `dc5782f8f880076874943b97ac181c29bb6f9cc2`. Verification pass. Review đọc diff và chạy assertion độc lập cho `greet()`.
2. Review gửi finding về docstring. Supervisor tiếp tục cùng ACP session. AGY thêm docstring. Revision mới `7caa5f6fb716b7fe8ef14bff21c16d717955d719` pass verification.
3. Review đọc file tại revision mới, chạy assertion độc lập kiểm tra cả return value và docstring, rồi gửi quyết định `ACCEPT`. SQLite ghi trạng thái `ACCEPTED`, hai revision và hai kết quả `PASSED`.

Một lần chạy mặc định với Gemini Argon gặp quota dù ACP trả `end_turn`. Supervisor giờ ghi `AGY_QUOTA` và `BLOCKED` cho thông báo đó. Cấu hình cài trên máy này dùng Gemini 3.8 Flash, model đã pass ca live. Ca thử ban đầu với lệnh terminal của AGY vượt thời gian chờ; ca live đã pass kiểm tra chỉnh file qua ACP và verification do Supervisor chạy.

Ca end-to-end với bản đã cài dùng task `3d8d113b4f124b5fb100d0a743a0b9dc`. Kế hoạch, permission, diff, finding và quyết định đều đi qua `mcpServer/tool/call`. Revision đầu `1d5d138ef8006ac564bf7be70bbdf5e888e59c18`, revision được nghiệm thu `6ff76f571a2e0a4e43af4c6e693948d8d92b43fb`. Assertion độc lập kiểm tra giá trị trả về và docstring. Repo gốc của ca thử giữ nguyên. Sau khi dừng và khởi động lại bằng Task Scheduler, Supervisor trả `READY` và SQLite vẫn trả đúng task `ACCEPTED` với revision đã nghiệm thu.

## Giới hạn đã ghi nhận

- Ca end-to-end gọi MCP bằng Codex App Server với kế hoạch và finding do bộ kiểm thử cung cấp. Chưa chạy một lượt qua giao diện chat Desktop để đo khả năng model tự chọn công cụ và tự lập kế hoạch.
- Task Scheduler đã được kích hoạt và kiểm tra tại chỗ. Chưa đăng xuất hoặc reboot Windows để kiểm tra trigger AtLogOn.
- Chưa kiểm thử trực tiếp tất cả toolchain dự án (Expo, Maven, Gradle) hay mọi cấu hình permission và sandbox của AGY.
- Chế độ nền giữ execution và review context. Người dùng mở lại chat để tiếp tục review theo task ID.

## Plugin gọi theo yêu cầu

- Codex nhận diện plugin `antigravity@agy-local` và skill `antigravity:agy`.
- Prompt gọi đích danh Antigravity đã nạp skill và đọc trạng thái `READY` từ Supervisor. Prompt viết hàm Python thông thường trả lời trực tiếp.
- Phiên kiểm thử có sandbox chặn localhost trả `IPC_UNAVAILABLE`. Kiểm tra trực tiếp trong phiên cục bộ có quyền loopback trả `READY`.
