# Codex AGY Supervisor

Công cụ điều phối Google Antigravity (AGY) từ Codex Desktop trên Windows. Chọn plugin **Antigravity** khi cần giao việc. Codex lập kế hoạch và review, AGY viết code trong Git worktree riêng, còn Supervisor chạy kiểm tra và lưu tiến độ trong SQLite.

## Cài đặt trên Windows

### Bước 1. Cài công cụ cần thiết

Trên Windows 10 hoặc 11, cần có **Git**, **Python 3.11 trở lên**, **Codex Desktop**, **Codex CLI** và **Antigravity CLI**. Mở PowerShell và kiểm tra từng lệnh:

```powershell
git --version
python --version
codex --version
agy --version
```

Nếu PowerShell báo không tìm thấy lệnh, cài công cụ tương ứng rồi mở lại PowerShell. Nếu đang dùng Python từ Microsoft Store, cài thêm `uv` hoặc dùng bản Python độc lập. [Cài Antigravity CLI](https://antigravity.google/docs/getting-started?tab=cli) · [Tài liệu plugin Codex](https://learn.chatgpt.com/docs/plugins)

### Bước 2. Đăng nhập Antigravity

Chạy `agy acp --help` để kiểm tra AGY có hỗ trợ ACP. Nếu lệnh chưa có, chạy `agy update` rồi kiểm tra lại. Nếu bạn đã đăng nhập AGY trên máy này, chuyển sang bước 3. Nếu chưa, chạy lệnh dưới đây và làm theo hướng dẫn trong terminal:

```powershell
agy acp --login
```

### Bước 3. Tải repo và chạy cài đặt

Mở PowerShell ở thư mục bạn muốn lưu tool, rồi chạy lần lượt:

```powershell
git clone https://github.com/Ducnguyen141224/codex-antigravity.git
Set-Location .\codex-antigravity
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Script tự cài thư viện Python, đăng ký plugin **Antigravity** và khởi động Supervisor. Nếu thấy `READY`, mở lại Codex Desktop và sang bước 4. Dòng `Config:` cho biết file cấu hình để bạn tìm lại khi cần.

### Bước 4. Thử trong Codex Desktop

Mở **chat cục bộ mới**, gõ `@` rồi chọn **Antigravity**. Bạn cũng có thể nhập trực tiếp:

> Dùng Antigravity để kiểm tra Supervisor có đang chạy không.

Nếu Codex báo `READY`, plugin đã kết nối được. Nếu chưa thấy plugin trong menu `@`, mở lại Codex Desktop và kiểm tra **Plugins → AGY Local**.

Muốn kiểm tra lại từ PowerShell, chạy hai lệnh sau trong thư mục tool:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke_test.ps1
codex plugin list --marketplace agy-local
```

Kết quả mong đợi là `READY`, `session_verified: true` và plugin `antigravity@agy-local` ở trạng thái `installed, enabled`.

## Sử dụng trong Codex Desktop

1. Mở **repo Git của dự án cần sửa** trong Codex Desktop. Repo cần ít nhất một commit vì AGY bắt đầu từ commit `HEAD`. Hãy commit trước các thay đổi mà AGY cần thấy.
2. Gõ `@`, chọn **Antigravity** và mô tả việc cần làm. Bạn cũng có thể viết prompt như sau:

   > Dùng Antigravity để thêm kiểm tra đầu vào cho hàm import CSV. Đọc repo trước, giữ API hiện tại, rồi review diff và chạy test độc lập trước khi nghiệm thu.

3. Codex xem diff, chạy test và yêu cầu AGY sửa nếu cần. Kết quả nằm trong worktree riêng. Sau khi nghiệm thu, bạn có thể áp dụng revision đã duyệt vào nhánh phát triển.
4. Nếu đã đóng chat, mở lại repo và nhắn **“Dùng Antigravity để tiếp tục review task <task_id>.”** Nếu quên ID, nhờ Antigravity liệt kê các task gần đây.

Plugin được thiết kế để dùng khi bạn gọi đích danh. Nếu repo có quy tắc cũ yêu cầu luôn ưu tiên AGY, xem [AGENTS.md](AGENTS.md) để cập nhật. Khi sandbox Codex chặn kết nối đến `127.0.0.1`, dùng cơ chế phê duyệt của Codex cho lệnh kết nối cục bộ.

## Kiến trúc

```text
Codex Desktop ── plugin Antigravity ── CLI cục bộ
                                         │
                                HTTP localhost + token
                                         │
                               Background Supervisor ── SQLite
                                 │               │
                                 │               └── Git worktree, diff, test
                                 └── ACP stdio ── Antigravity CLI
```

Supervisor tiếp tục chạy khi đóng chat. Nếu Supervisor bị dừng giữa lượt, lần khởi động sau đánh dấu `INTERRUPTED` và giữ lại worktree. Kiểm tra worktree trước khi dùng `retry_task`. Plugin gọi các thao tác qua CLI. Có thể chạy adapter MCP stdio riêng cho tích hợp khác.

## Theo dõi và review

Codex lập một kế hoạch trước khi giao việc và giữ phiên bản đó trong các milestone và vòng sửa. Plugin ưu tiên tool MCP `agy_wait / wait_for_review` để chờ đến khi cần review, cần quyền hoặc task kết thúc. Một lời gọi được giữ tối đa 1800 giây, với timeout của host là 1860 giây. Supervisor theo dõi sự kiện ACP, còn `get_task_result` trả trạng thái và kết quả tóm tắt. Diff và kết quả kiểm thử được lưu theo revision. Codex đọc Git diff trước, giao AGY sửa các lỗi ảnh hưởng đến nghiệm thu, rồi xem `fix_diff` và kiểm tra nguy cơ hồi quy ở vòng sau.

CLI `wait <task_id>` cũng giữ một kết nối HTTP theo sự kiện. Tuy nhiên, host shell hoặc code-mode có thể trả handle sau một khoảng ngắn và Codex cần thêm lượt model để tiếp tục đợi. Plugin hướng dẫn tăng `yield_time_ms` của code-mode khi host hỗ trợ. Cài lại plugin bằng `scripts\install_plugin.ps1` và mở chat mới để nạp tool MCP chờ. Xem [kết quả đo thực tế](WAITING_AUDIT.md) về số lượt model và token.

`submit_plan` nhận đối tượng `plan` theo schema. Xem [kế hoạch mẫu](examples/plan.example.json) hoặc chạy `.\.venv\Scripts\python.exe -m codex_agy.cli schema submit_plan`. `execute_plan` lên lịch toàn bộ kế hoạch và chờ review giữa các milestone. `execute_milestone` chạy từng milestone qua cùng `execution_id`. `revise_plan` tạo phiên bản mới cho các execution sau.

Dùng Hybrid: AGY làm theo phase lớn, Codex review tại checkpoint quan trọng. Mỗi milestone trong TaskPlan là một checkpoint và có thể chứa nhiều phase dưới dạng các task có dependency. Codex ghi rõ lý do và tiêu chí của từng checkpoint khi lập plan, dựa trên phạm vi, dependency, rủi ro và giới hạn thực thi.

| Quy mô | Cách đặt checkpoint |
| --- | --- |
| Nhỏ, như sửa UI hoặc CRUD đơn giản | AGY làm toàn bộ rồi Codex review một lượt. |
| Trung bình, thêm chức năng hoàn chỉnh | Ưu tiên một checkpoint giữa quá trình và một review cuối. |
| Lớn, xây module hoặc hệ thống | Review tại các ranh giới kiến trúc và tích hợp. |
| Rủi ro cao, như authentication, payment, migration | Review trước khi giao AGY bước quan trọng tiếp theo. |

Số checkpoint được quyết định cho từng công việc. Với plan có sẵn, giữ nội dung và thứ tự phase, gom các phase liên quan thành checkpoint phù hợp và tôn trọng checkpoint người dùng yêu cầu rõ. AGY hoàn thành toàn bộ task của checkpoint rồi Supervisor chạy kiểm thử nghiệm thu và Codex review. Codex gom lỗi ảnh hưởng tiêu chí thành một yêu cầu sửa. Plan đã chạy giữ nguyên phiên bản và ranh giới milestone.

Để Codex chỉ bắt đầu review sau khi kiểm thử bắt buộc đã đạt, đặt `verification_gate: true` trong plan mới. Mỗi milestone cần ít nhất một lệnh kiểm thử khai báo rõ. Có thể đánh dấu `auto_fix_on_failure: true` trên lệnh kiểm thử chức năng để Supervisor gom các lỗi và giao AGY sửa một lần trong lúc Codex vẫn chờ. Sau bản sửa, Supervisor kiểm tra lại các lệnh bắt buộc trên revision mới. Hãy kiểm tra môi trường và fixture trước khi giao việc, và để `auto_fix_on_failure` tắt trên các lệnh kiểm tra môi trường. Nếu gặp lỗi môi trường, kiểm thử vẫn thất bại sau lần sửa, hoặc hết giới hạn vòng sửa, Supervisor trả `VERIFICATION_BLOCKED` và giữ worktree. Plan cũ mặc định giữ quy trình review như trước.

Khi đến lượt review, Codex gom các lỗi ảnh hưởng đến tiêu chí vào một yêu cầu sửa. Ở vòng sau, ưu tiên `fix_diff`, kết quả test của revision mới và các hành vi có nguy cơ hồi quy. Khi hết giới hạn review, báo các lỗi còn lại.

Khai báo `preflight_commands` ở cấp plan hoặc task để kiểm tra môi trường và fixture trước mỗi lượt AGY. Supervisor lưu kết quả trong thư mục artifacts. Nếu một lệnh thất bại, task báo `PREFLIGHT_FAILED` trước khi mở phiên AGY. Các lệnh này nên kiểm tra cấu hình, kết nối và dữ liệu kiểm thử sẵn có, tránh sửa file được Git theo dõi. Ví dụ `"preflight_commands": [{"argv": ["python", "-c", "import sqlite3"]}]`. Các trường mới là tùy chọn để plan cũ tiếp tục hoạt động. Tự sửa kiểm thử được giới hạn một lần cho mỗi milestone, đồng thời tuân theo giới hạn review hiện có.

| Công việc | Thao tác |
| --- | --- |
| Lập và chạy kế hoạch | `submit_plan`, `revise_plan`, `execute_plan`, `execute_milestone` |
| Theo dõi | `list_tasks`, `get_task_status`, `get_task_result`, `wait_for_completion` |
| Review | `get_review_context`, `request_fix`, `accept_milestone`, `reject_milestone` |
| Vận hành | `respond_permission`, `cancel_task`, `retry_task`, `health_check`, `get_system_capabilities` |

Với `get_review_context`, dùng `view="summary"`, `view="diff"`, `view="fix_diff"` cho phần thay đổi kể từ vòng trước, hoặc `view="file"` kèm đường dẫn tương đối. Dùng `review_revision` để đọc snapshot cũ. Diff và file hỗ trợ phân trang qua `offset` và `next_offset`. Mỗi quyết định review phải gửi revision và ghi chú kiểm tra. Nếu worktree đổi sau snapshot, kiểm tra lại thay đổi trước khi gửi quyết định.

Verification chạy các lệnh argv trong TaskPlan. Khi thiếu lệnh, Supervisor tìm các cấu hình Python, Node, Maven và Gradle có sẵn trong repo. `FAILED` và `ERROR` chặn nghiệm thu. `SKIPPED` và `NOT_AVAILABLE` xuất hiện trong kết quả, Codex cần kiểm tra tiêu chí liên quan bằng phương pháp độc lập trước khi nghiệm thu. Cài dependency theo cách của project trong worktree khi cần.

Supervisor tự xử lý quyền ACP theo `permission_mode` trong `config.yaml`. Quyền tự duyệt được ghi lại trong dữ liệu Supervisor và lời gọi chờ vẫn mở.

| Chế độ | Cách hoạt động |
| --- | --- |
| `worktree` (mặc định) | Khi bật sandbox, tự duyệt đọc trong worktree, sửa file nằm trong kế hoạch và một số lệnh kiểm tra Git/thư mục có dạng cố định. |
| `manual` | Trả `WAITING_PERMISSION` để xem và xử lý từng yêu cầu bằng `respond_permission`. |
| `allow_all` | Python duyệt mọi yêu cầu ACP có lựa chọn `allow_once`, kể cả lệnh shell. |

Muốn chạy AGY với toàn quyền trên máy local, đặt `permission_mode: allow_all` và `sandbox: false`, sau đó khởi động lại Supervisor. Thiết lập này cho AGY thực thi với quyền của tài khoản hệ điều hành đang chạy Supervisor. Khi AGY cần thông tin bổ sung, Supervisor ghi blocker và giữ worktree.

## Vận hành và xử lý lỗi

```powershell
.\.venv\Scripts\python.exe -m codex_agy.cli health
.\.venv\Scripts\python.exe -m codex_agy.cli call list_tasks
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_supervisor.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_supervisor.ps1
```

| Lỗi | Cách xử lý |
| --- | --- |
| `AUTH_REQUIRED` | Chạy `agy acp --login`, sau đó chạy lại `smoke_test.ps1`. |
| `ACP_UNAVAILABLE` | Cập nhật bằng `agy update`, rồi kiểm tra lại `agy acp --help`. |
| `IPC_UNAVAILABLE` | Chạy `start_supervisor.ps1` và xem `supervisor.log` trong thư mục dữ liệu. Nếu chỉ gặp lỗi trong Codex, kiểm tra quyền kết nối localhost. |
| `AGY_QUOTA` | Chờ quota của tài khoản được cấp lại hoặc chọn model ACP khác qua `agy_model` trong YAML. Sau khi hết lượt đang chạy, dừng rồi khởi động Supervisor để nạp cấu hình. |

Dùng `-ConfigPath` cho file cấu hình riêng và truyền lại cùng đường dẫn cho các script vận hành. Installer cũng hỗ trợ `-PythonExecutable`, `-NoStartup` và `-NoCodexRegistration`. Dữ liệu, log và worktree nằm tại `data_dir` trong YAML. Nếu dùng Gemini API key, đặt `GEMINI_API_KEY` trong môi trường khởi chạy Supervisor và giữ key ngoài Git.

Gỡ plugin và tác vụ khởi động bằng lệnh dưới đây. Dữ liệu và worktree được giữ lại.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1
```

### Phạm vi cách ly

Supervisor dùng AGY terminal sandbox, yêu cầu quyền ACP có quyết định theo từng lần, lọc môi trường kế thừa và dùng Git worktree riêng. Quy tắc đã thiết lập trong AGY có thể tự cho phép một số hành động. Worktree chia sẻ metadata Git với repo gốc. Các lệnh verification chạy với tài khoản Windows hiện tại và có thể chạy code từ repo. Chỉ dùng tool với repo và lệnh verification đáng tin cậy. Có thể giới hạn repo qua `allowed_roots`. Windows Job Object dừng cây tiến trình khi worker thoát; đây là giới hạn vòng đời tiến trình, không phải sandbox filesystem.

### Test

```powershell
uv pip install --python .venv\Scripts\python.exe -e '.[test]'
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\python.exe -m pytest -q -m 'not live'

$env:CODEX_AGY_LIVE = '1'
$env:CODEX_AGY_LIVE_MODEL = 'gemini-3.8-flash'
.\.venv\Scripts\python.exe -m pytest -s -q -m live
```

Các test thường dùng fixture ACP và kiểm tra IPC, schema, lưu trạng thái, Git isolation, permission, cancellation và recovery. Test `live` tạo repo tạm, chạy AGY thật và dùng tài khoản đang đăng nhập. Xem [kết quả kiểm thử](TESTING.md) để biết những đường chạy đã xác minh trên máy này.

## Source

| Đường dẫn | Vai trò |
| --- | --- |
| `src/codex_agy/cli.py`, `config.py` | Entry points, cấu hình, probe, startup |
| `src/codex_agy/mcp_server.py`, `api.py`, `ipc.py` | MCP tools, schema và HTTP IPC |
| `src/codex_agy/acp_client.py`, `process.py` | AGY ACP, watchdog và vòng đời tiến trình |
| `src/codex_agy/supervisor.py`, `storage.py` | Scheduler, state machine, permission và SQLite |
| `src/codex_agy/planning.py` | TaskPlan và dependency |
| `src/codex_agy/workspace.py`, `verification.py` | Worktree, snapshot, diff và kiểm tra |
| `scripts/`, `tests/`, `examples/` | Cài đặt Windows, test và kế hoạch mẫu |

## Cấu hình Hybrid và số đo

Plan mới dùng `review_strategy: "hybrid"`, `work_size: "small"`, `"medium"` hoặc `"large"`, và `verification_gate: true`. Việc nhỏ mặc định một checkpoint cuối, việc trung bình hai checkpoint. Nếu cần số khác, ghi lý do vào `checkpoint_exception_reason`. Mỗi milestone vẫn là một checkpoint, có thể chứa nhiều phase/task.

Đánh dấu task quan trọng bằng `critical_change` có giá trị `architecture`, `database`, `authentication`, `payment` hoặc `integration`. Task này và task có `risk_level: "high"` phải nằm sau một milestone tiền nhiệm được review, chẳng hạn duyệt thiết kế hoặc hợp đồng API trước khi triển khai. Supervisor kiểm tra dependency đã khai báo và chặn bước tiếp theo đến khi được nghiệm thu. Việc nhận diện đúng rủi ro trong nội dung công việc vẫn thuộc trách nhiệm lập plan của Codex. Các plan cũ giữ chế độ `legacy` để tiếp tục tương thích.

`get_execution_metrics` nhận `execution_id` và trả số lần vào review, yêu cầu sửa của Codex, lượt tự sửa, số MCP và IPC gắn với task. MCP được đếm tại wrapper của Supervisor, IPC bao gồm request do MCP chuyển tiếp nên hai số này có thể trùng hoạt động. Bộ đếm có hiệu lực từ khi cài tính năng; token Codex trả `null` vì Supervisor không nhận usage từ host. Đây là số đo giao thức, không suy ra phần trăm quota. Giới hạn vòng review được lưu riêng cho mỗi execution khi tạo để thay cấu hình sau đó không tự tăng số vòng.
