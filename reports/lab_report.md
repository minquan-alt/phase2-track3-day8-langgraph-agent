# Day 08 Lab Report — LangGraph Agentic Orchestration

## 1. Team / Student

- **Name:** Hoang Ba Minh Quang
- **Email:** hbminhquang.19@gmail.com
- **Repo/commit:** `f08f8ab` (branch: main)
- **Date:** 2026-05-12

---

## 2. Architecture

Graph xây dựng theo mô hình **State Machine** với 11 node và 4 conditional routing function. Luồng xử lý chính:

```
START → intake → classify → [conditional route]

  simple       → answer → finalize → END
  tool         → tool → evaluate → answer → finalize → END
  tool (retry) → tool → evaluate → retry → tool → evaluate → ... (bounded loop)
  missing_info → clarify → finalize → END
  risky        → risky_action → approval → tool → evaluate → answer → finalize → END
  error        → retry → tool → evaluate → retry → ... → dead_letter → finalize → END
```

**Các node chính:**

| Node | Vai trò |
|---|---|
| `intake` | Chuẩn hóa query (strip whitespace, collapse spaces) |
| `classify` | Phân loại route theo keyword heuristic (có priority) |
| `answer` | Sinh câu trả lời từ tool_results hoặc approval |
| `tool` | Gọi mock tool, mô phỏng transient failure cho error scenarios |
| `evaluate` | Kiểm tra tool result — "done?" check cho retry loop |
| `clarify` | Trả về câu hỏi làm rõ khi thiếu thông tin |
| `risky_action` | Chuẩn bị proposed_action trước khi gửi approval |
| `approval` | HITL step: mock approve mặc định, dùng `interrupt()` khi `LANGGRAPH_INTERRUPT=true` |
| `retry_or_fallback` | Tăng attempt counter, ghi backoff metadata |
| `dead_letter` | Ghi nhận failure không thể recover, log để review thủ công |
| `finalize` | Emit audit event cuối, mọi path đều đi qua đây trước END |

---

## 3. State Schema

`AgentState` là `TypedDict` với hai loại field: **overwrite** (ghi đè) và **append-only** (dùng `Annotated[list, add]` reducer).

| Field | Reducer | Lý do |
|---|---|---|
| `thread_id` | overwrite | Định danh duy nhất mỗi run |
| `scenario_id` | overwrite | Tracking cho metrics |
| `query` | overwrite | Chỉ cần giá trị hiện tại |
| `route` | overwrite | Chỉ cần route hiện tại để routing |
| `risk_level` | overwrite | Đặc trưng của query, không thay đổi trong run |
| `attempt` | overwrite | Counter tăng dần, cần giá trị mới nhất |
| `max_attempts` | overwrite | Config constant |
| `final_answer` | overwrite | Câu trả lời cuối cùng |
| `pending_question` | overwrite | Câu hỏi làm rõ hiện tại |
| `proposed_action` | overwrite | Action cần approval |
| `approval` | overwrite | Quyết định approval gần nhất |
| `evaluation_result` | overwrite | Kết quả đánh giá tool — gate cho retry loop |
| `messages` | **append** (`add`) | Audit trail toàn bộ conversation |
| `tool_results` | **append** (`add`) | Lịch sử tool calls |
| `errors` | **append** (`add`) | Tích lũy lỗi, không overwrite để debug |
| `events` | **append** (`add`) | Audit events cho metrics và grading |

**Lý do thiết kế:** `evaluation_result` là overwrite (không phải append) vì routing function `route_after_evaluate` cần đọc kết quả của lần evaluate **gần nhất** — nếu append thì phải đọc phần tử cuối, dễ gây bug.

---

## 4. Scenario Results

### Standard Scenarios (15/15 passed)

| Scenario | Expected Route | Actual Route | Success | Retries | Interrupts |
|---|---|---:|---:|---:|---:|
| G01_simple | simple | simple | ✅ | 0 | 0 |
| G02_simple2 | simple | simple | ✅ | 0 | 0 |
| G03_tool | tool | tool | ✅ | 0 | 0 |
| G04_tool2 | tool | tool | ✅ | 0 | 0 |
| G05_tool3 | tool | tool | ✅ | 0 | 0 |
| G06_missing | missing_info | missing_info | ✅ | 0 | 0 |
| G07_missing2 | missing_info | missing_info | ✅ | 0 | 0 |
| G08_risky | risky | risky | ✅ | 0 | 2 |
| G09_risky2 | risky | risky | ✅ | 0 | 2 |
| G10_risky3 | risky | risky | ✅ | 0 | 2 |
| G11_risky4 | risky | risky | ✅ | 0 | 2 |
| G12_error | error | error | ✅ | 4 | 0 |
| G13_error2 | error | error | ✅ | 4 | 0 |
| G14_dead | error | error | ✅ | 2 | 0 |
| G15_mixed | risky | risky | ✅ | 0 | 2 |

**Tổng kết:**
- Total scenarios: 15 | Success rate: **100%**
- Avg nodes visited: 13.20 | Total retries: 10 | Total interrupts: 10
- `resume_success: true`

### Hard Dataset (25/25 passed)

| Nhóm | Số scenarios | Success |
|---|---:|---:|
| simple | 4 | 4/4 |
| tool | 6 | 6/6 |
| missing_info | 3 | 3/3 |
| risky | 7 | 7/7 |
| error (retry) | 3 | 3/3 |
| error (dead letter) | 2 | 2/2 |

**Tổng kết:**
- Total scenarios: 25 | Success rate: **100%**
- Avg nodes visited: 13.20 | Total retries: 22 | Total interrupts: 12

---

## 5. Failure Analysis

### Failure Mode 1: Retry Loop — Tool Transient Failure

**Kịch bản:** Query chứa keyword `timeout`, `fail`, `error`, `crash`, `unavailable` → route `error` → vào `retry` node trước khi gọi tool.

**Cơ chế:**
```
retry → tool → evaluate → [needs_retry?] → retry → tool → evaluate → ...
                                         → [success] → answer → finalize
                                         → [attempt >= max] → dead_letter → finalize
```

- `tool_node` mô phỏng transient failure khi `route == error` và `attempt < 2`
- `evaluate_node` detect `status=error` hoặc `transient failure` trong tool result
- `retry_or_fallback_node` tăng attempt và ghi backoff metadata (`attempt=1 backoff=1s`, `attempt=2 backoff=2s`)
- Loop bị chặn bởi `route_after_retry`: nếu `attempt >= max_attempts` → `dead_letter`

**Dead letter (G14, H17):** `max_attempts=1` → sau 1 lần retry thất bại, đi thẳng vào `dead_letter_node`, ghi log `dead-letter exhausted retries at attempt=1`, finalize với thông báo escalation.

**Rủi ro nếu thiếu bound:** Không có `max_attempts` check → infinite loop. Đây là lý do `evaluation_result` phải là overwrite: nếu append thì phải dùng `[-1]` và dễ bị stale data từ lần evaluate trước.

### Failure Mode 2: Risky Action — Approval Path

**Kịch bản:** Query chứa keyword `refund`, `delete`, `send`, `cancel`, `remove`, `revoke` → route `risky` → bắt buộc có approval trước khi thực thi tool.

**Cơ chế:**
```
classify(risky) → risky_action → approval → [approved?] → tool → evaluate → answer
                                           → [rejected]  → clarify → finalize
```

- `risky_action_node` tạo `proposed_action` với context đầy đủ để reviewer có thể quyết định
- `approval_node` dùng `interrupt()` khi `LANGGRAPH_INTERRUPT=true` (HITL thật), mock approve khi offline
- `route_after_approval` check `approval.approved` — nếu rejected → `clarify` (không thực thi action)
- Metric `approval_required=True` + `approval_observed=True` xác nhận path đã kích hoạt đúng

**Rủi ro nếu skip approval:** Action nguy hiểm (refund, delete, revoke) chạy mà không có kiểm soát. LangGraph `interrupt()` đảm bảo graph dừng hoàn toàn cho đến khi human resume.

### Failure Mode 3: Word Boundary False Positive

Classify dùng `re.findall(r"\b\w+\b", normalized)` để tokenize. Điều này ngăn "item" match "it", "itself" match "it". Scenario `H09_simple_word_boundary_item` kiểm tra đúng edge case này.

---

## 6. Persistence / Recovery Evidence

**Checkpointer:** SQLite với WAL mode (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`), file `checkpoints.db`.

```yaml
# configs/lab.yaml
checkpointer: sqlite
database_url: checkpoints.db
```

**Thread ID:** Mỗi scenario có `thread_id = "thread-{scenario.id}"` → state được persist riêng biệt, không bị overwrite giữa các runs.

**State History:** `cli.py` gọi `graph.get_state_history(config=run_config)` sau mỗi scenario. Nếu history không rỗng → `resume_success = True`. Cả hai metrics file đều có `"resume_success": true`.

**Crash-Resume demo:** `make demo-crash-start` / `make demo-crash-resume` dùng cùng `thread_id` với SQLite checkpoint để simulate restart giữa chừng:

```bash
# Phase 1: Start risky flow, pause tại interrupt
make demo-crash-start
# → thread interrupted tại approval node, trạng thái được lưu vào checkpoints.db

# Phase 2: Sau "restart" (process kill), resume từ checkpoint
make demo-crash-resume
# → graph tiếp tục từ approval với decision "approve"
```

**Time-travel:** `make demo-time-travel` gọi `get_state_history()` và in danh sách checkpoints với `checkpoint_id`, `route`, `attempt`, `last_node` — cho phép replay từ bất kỳ điểm nào trong lịch sử.

---

## 7. Extension Work

### Extension 1: SQLite Persistence (hoàn thành)

- `persistence.py` implement `build_checkpointer(kind="sqlite")` với `SqliteSaver(conn=...)`, WAL mode, và connection pool `_OPEN_SQLITE_CONNECTIONS`
- State survive restart vì SQLite được persist trên disk
- `checkpoints.db` được tạo tại working directory

### Extension 2: Real HITL với `interrupt()` (hoàn thành)

- `approval_node` check env var `LANGGRAPH_INTERRUPT=true` để kích hoạt `interrupt()` thật
- Support 4 action: `approve`, `reject`, `edit`, `timeout`
- `edit` action cho phép reviewer sửa `proposed_action` trước khi approve
- `timeout` action tạo `ApprovalDecision(approved=False)` và ghi error

### Extension 3: Crash-Resume Demo (hoàn thành)

- `cli.py` có command `demo-crash-recover` với `--phase start/resume`
- Phase start: khởi động risky flow với `LANGGRAPH_INTERRUPT=true`, dừng tại approval
- Phase resume: load lại checkpoint từ SQLite, tiếp tục với decision

### Extension 4: Time-Travel Replay (hoàn thành)

- `demo-time-travel` command print full checkpoint history với `get_state_history()`
- Mỗi snapshot có: `checkpoint_id`, `created_at`, `next_nodes`, `route`, `attempt`, `last_node`
- Dùng để debug và audit toàn bộ execution path

---

## 8. Improvement Plan

Nếu có thêm một ngày, ưu tiên theo thứ tự:

### 1. LLM-based Classifier (ưu tiên cao nhất)

Hiện tại `classify_node` dùng keyword heuristic đơn giản. Trong production, cần thay bằng LLM call (có structured output) để xử lý được:
- Tiếng Việt và ngôn ngữ khác (H22 test case)
- Queries phức tạp với nhiều intent đan xen
- Edge cases không thể enumerate bằng keyword

### 2. Real Tool Integration

`tool_node` hiện mock tất cả kết quả. Cần:
- Gọi API thật (order system, CRM)
- Structured tool result schema với Pydantic
- Proper error classification (transient vs permanent)

### 3. LLM-as-Judge cho Evaluate

`evaluate_node` dùng string matching để detect failure. Cần LLM judge để:
- Đánh giá chất lượng kết quả (không chỉ success/error)
- Handle partial success
- Quyết định retry strategy thông minh hơn

### 4. Parallel Fan-out với `Send()`

Cho tool scenarios phức tạp cần lookup nhiều source, dùng `Send()` API để:
- Gọi 2-3 tools song song
- Merge results qua `add` reducer
- Giảm latency đáng kể

### 5. Observability

- Tích hợp LangSmith tracing để trace từng node trong production
- Alerting khi dead_letter rate tăng cao
- Dashboard metrics real-time từ SQLite/Postgres checkpointer

---

## Appendix: Graph Diagram

```
START
  │
  ▼
intake ──────────────────────────────────────────────────────────┐
  │                                                              │
  ▼                                                              │
classify                                                         │
  │                                                              │
  ├─[simple]──────────► answer ──────────────────────────────┐  │
  │                                                           │  │
  ├─[tool]────────────► tool ──► evaluate ──[success]───────►│  │
  │                                   │                       │  │
  │                               [needs_retry]               │  │
  │                                   │                       │  │
  │                                   ▼                       │  │
  │                              retry_or_fallback            │  │
  │                               [attempt<max]──► tool       │  │
  │                               [attempt>=max]──► dead_letter│  │
  │                                                           │  │
  ├─[missing_info]────► clarify ─────────────────────────────►│  │
  │                                                           │  │
  ├─[risky]───────────► risky_action ──► approval            │  │
  │                                        │                  │  │
  │                                    [approved]──► tool     │  │
  │                                    [rejected]──► clarify  │  │
  │                                                           │  │
  └─[error]───────────► retry_or_fallback ──► (see tool loop)│  │
                                                              │  │
                                                              ▼  │
                                                           finalize
                                                              │
                                                              ▼
                                                             END
```
