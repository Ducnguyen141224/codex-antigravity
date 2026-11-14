# Quy trình Codex và Antigravity

- Dùng plugin Antigravity khi người dùng chọn plugin bằng `@` hoặc yêu cầu rõ dùng Antigravity, AGY hay `codex_agy`. Xử lý các yêu cầu khác theo quy trình Codex thông thường.
- Đọc repo trước, xác định phạm vi, các file liên quan, test hiện có và thay đổi chưa commit. Execution lấy base từ commit HEAD nên phải tính đến chênh lệch với working tree gốc.
- Viết TaskPlan theo schema của `submit_plan`. Chia milestone theo chức năng, đặt dependency và acceptance criteria kiểm được. Gửi lệnh verification phù hợp dưới dạng argv.
- Dùng `execute_plan`, giữ lại execution ID và task ID. Dùng `wait_for_completion` với thời hạn ngắn để tiếp tục review trong cùng lượt. Nếu cần quay lại sau, dùng task ID hoặc `list_tasks`.
- Với `WAITING_PERMISSION`, xem thao tác cụ thể và phạm vi đã được cấp quyền trước khi chọn. Với `BLOCKED`, `INTERRUPTED` hoặc lỗi, đọc trạng thái, log liên quan và worktree để quyết định bước tiếp theo.
- Khi `REVIEW_REQUIRED`, lấy review revision, đọc diff và source cần thiết. Đối chiếu tiêu chí và chạy test độc lập. Gửi findings có thể tái hiện bằng `request_fix` hoặc nghiệm thu đúng revision bằng `accept_milestone`.
- Khi trả về `next_task_id`, tiếp tục vòng thực thi và review. Kết thúc khi mọi milestone đã nghiệm thu hoặc có blocker khách quan. Báo các file, revision, test và phần chưa kiểm chứng.
- Giữ lại worktree để review. Push, merge và deploy chỉ theo yêu cầu đã được cấp quyền.
