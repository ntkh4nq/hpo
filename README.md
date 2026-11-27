# HPO: trợ lý chuẩn hóa biểu hiện lâm sàng tiền sản

Hệ thống đọc mô tả siêu âm/MRI/khám thai bằng tiếng Việt, chuẩn hóa sang HPO, tra bệnh và gene liên quan, rồi đưa gợi ý kèm nguồn để bác sĩ xem xét. **Hệ thống chỉ hỗ trợ tra cứu, không chẩn đoán bệnh.**


## Chuẩn bị

- Python và [uv](https://docs.astral.sh/uv/), Node.js và pnpm.
- File `.env` ở thư mục gốc, điền:
  - `DATABASE_URL`, `REDIS_URL`: URL public của Postgres và Redis trên Railway.
  - `LLM_BASE_URL`, `LLM_API_KEY`: gateway LiteLLM, dùng virtual key của project hpo.
  - `LANGFUSE_*`: để trống nếu không cần trace.


## Chạy app

```bash
make api      # Terminal 1: API FastAPI tại http://127.0.0.1:8000 (OpenAPI ở /docs)
make web      # Terminal 2: frontend tại http://127.0.0.1:5173 (cần make api)
```

Lần đầu cài frontend: `cd frontend && pnpm install`.

Để chạy lại hay thử một ca mà không tốn request DeepSeek, dùng `make api-replay` thay cho `make api`. Lệnh này lưu câu trả lời model và dùng lại khi đầu vào không đổi.

## Lệnh thường dùng

| Lệnh | Khi nào dùng |
|---|---|
| `make migrate` | Khi có file mới trong `backend/db/migrations/`. |
| `make seed` | Dựng KB lớp 1–2 (chạy lại an toàn, khoảng 30 giây). Cần `data/raw/translations/HPO_review_doctor_full.xlsx`. |
| `make seed-download` | Tải HPO release mới nhất vào `data/raw/hpo/<tag>/`. |
| `make embed` | Điền embedding cho tên HPO (chạy tiếp được nếu bị ngắt). |
| `make test` | Chạy pytest trên DB chung. Dữ liệu test có prefix riêng và tự xoá. |
| `make analyze TEXT="..."` | Chạy graph phân tích một mô tả. |
| `make rank TERMS="HP:0001629 HP:0001561?"` | Xếp hạng bệnh/gene từ mã HPO.  |
| `make probe` | Kiểm tra model Qwen và BGE-M3 qua gateway. |

Frontend: `cd frontend && pnpm test` (test reducer), `pnpm build` (kiểm kiểu và build).


