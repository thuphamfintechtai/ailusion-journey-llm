# Kiểm chứng prompt Agent Planner (bản nháp mới) với code — 10/2026

Đối chiếu bản nháp *"AGENT PLANNER — LẬP CÁC PHƯƠNG ÁN LỊCH TRÌNH DU LỊCH"* và checklist kiểm chứng với
code hiện tại (`dev`). Đường dẫn tính từ `src/ailusion/itinerary/`. Ký hiệu: ✅ khớp · ⚠️ lệch / mơ hồ ·
❌ lỗi / thiếu.

## Kết luận (mục 11 của checklist)

1. **Mâu thuẫn với backend: có.**
   - Bản nháp nói "tránh nhiều chỗ cùng loại *nếu* làm giảm đa dạng". Code coi 2 chỗ chơi cùng loại trong một lịch là **lỗi**: `plan_errors` ghi lỗi, `drop_same_group` bỏ chỗ sau.
   - "Spa sau 13:00 *nếu quy tắc hệ thống yêu cầu*": model không biết quy tắc đó. Code luôn coi spa mở từ 13:00.
   - "Dành khoảng nghỉ trưa": code **tự chèn** 90 phút, Planner không được để trống chỗ cho nó.
2. **Lặp giữa prompt và backend: có, ở cả 2 bản.** Phần bữa ăn: dời quán vào đúng giờ, bỏ quán thừa, 2 bữa cách nhau. Phần cùng loại, chờ lâu, chỗ buổi tối. Code đều đã làm. Bản nháp còn nói thêm "backend sẽ sửa", tức là lặp mà vô ích.
3. **Không thực thi được vì thiếu dữ liệu đầu vào: có.**
   - Bản nháp gọi các nhãn bằng tên khác với chữ thật trong input: "thẻ phù hợp mục đích", "thẻ hoạt động buổi tối", "thẻ bắt buộc", "thẻ người dùng nhắc đến", `day_target`. Input thật ghi `| hợp mục đích`, `| đi buổi tối`, `| BẮT BUỘC | user muốn tới …`, `| user nhắc: …`, `# Tiêu khoảng X đ mỗi ngày`.
   - Không nói D… là chỗ chơi, Q… là quán.
   - "Phạm vi duy nhất của `id`" và "quy tắc hệ thống hiện tại" là những thứ model không có cách biết.
4. **Chỗ bắt buộc không xếp được:** bước lọc (`loc`) bỏ chỗ đóng cửa cả chuyến và ghi chú "Không xếp được". `moved_closed_days` dời sang ngày còn mở. Còn thiếu thì `check_plan` đánh **fail** với lỗi "thiếu chỗ bạn chọn". Không bước hậu xử lý nào xoá chỗ bắt buộc.
5. **Không đủ 2 phương án mỗi ngày:**
   - `plan_errors` chỉ **ghi nhãn**, vì lượt sửa đang tắt.
   - Ngày không có lịch nào thì `fallback_plans` tự ghép 1 lịch dự phòng.
   - Nhánh lỗi được chạy lại 1 lần rồi bỏ. Ghép phương án vẫn chạy với số lịch hiện có.
6. **Ngân sách khớp prompt hiện tại**, sau bản sửa ngân sách:
   - `# Tiêu khoảng X` là `day_allowance` = tiền cả ngày (vé + ăn + đi lại) cho cả đoàn, chưa gồm chỗ ở và phát sinh.
   - Bước chạy thử so lịch với đúng số này. Vượt thì gắn `warn`, không loại.
   - Bản nháp khớp về ý nhưng gọi sai tên (`day_target` thay vì dòng "Tiêu khoảng").
7. **Thay ngay được và cần sửa code trước:**
   - **Thay prompt ngay** *(đã làm)*: thêm 3 luật vào luật 5 của prompt **hiện tại**: không 2 chỗ chơi cùng loại trong một lịch; `id` L1, L2… không trùng trong ngày; ưu tiên chỗ ghi "hợp mục đích".
   - **Không thay nguyên bản nháp:** dài gấp 3,5 lần (10.816 so với 3.075 ký tự), mất các con số cụ thể (90 / 45 / 150 phút, tối thiểu 2 chỗ chơi), và không nhắc tới chữ thật trong input.
   - **Sửa code trước** *(đã làm)*: `id` trùng trong một ngày từng làm mất lịch mà không báo. Output sai kiểu một trường từng làm mất cả lô tới 4 ngày (xem mục 1).
8. **Đủ bằng chứng để triển khai bản nháp: không, và không nên.** A/B (mục 10), 5 chuyến × 3 nhánh:
   - Prompt hiện tại thêm 3 luật giảm lỗi luật từ 21 xuống 8, cùng chi phí.
   - Bản nháp còn 12 lỗi, tốn hơn 44% token.
   - Khoảng 42% lịch fail ở **cả 3 bản**, do 3 lỗi code (mục 10). Phải sửa code trước thì mới so được prompt theo tỉ lệ fail.

---

## 1. Hợp đồng đầu vào / đầu ra

| | Kết quả | Bằng chứng |
|---|---|---|
| Schema | ✅ `PlannerOutput{days: [DayDrafts{day ≥ 1, plans: [PlanDraft{id, leave_stay, stops[]}]}]}` | agents/planner.py |
| Kiểu / định dạng | ✅ structured output qua function calling (không phải JSON thuần — dòng "không trả Markdown" của bản nháp vô nghĩa) | `ctx.ask`, shared/context.py |
| Phạm vi `id` | ✅ *(đã sửa)* duy nhất **trong ngày** (`N{ngày}-{id}`, sau ghép `V{nhánh}-N1-L1`). Trước đây `plan_errors` không bắt id trùng và `plans[plan.id] = …` ghi đè → lịch thứ hai mất im lặng. Nay `plan_errors` báo "mã lịch … trùng nhau trong ngày" / "có lịch không có mã", `xep_lich` đặt mã khác (`N1-L1-2`) thay vì ghi đè; prompt luật 5 nói rõ | `xep_lich`, `plan_errors` |
| `leave_stay` | ✅ `HH:MM`, không sớm hơn giờ bắt đầu ngày (`plan_errors`); sai dạng thì code dùng giờ bắt đầu ngày | `xep_lich` |
| `stops` | ✅ chỉ mã trong danh sách và thuộc ngày đó (`plan_errors` ghi lỗi); khi dựng lịch code **bỏ im lặng** mã lạ và mã lặp (`dict.fromkeys`) | `xep_lich` |
| JSON sai / thiếu trường | ✅ *(đã sửa)* trước đây một trường sai kiểu → Pydantic từ chối → `ctx.ask` trả None → **mất cả lô** (tối đa 4 ngày) → mỗi ngày 1 lịch dự phòng. Nay `PlanDraft` đổi `id` / `leave_stay` null hoặc số thành chuỗi, `stops` dạng chuỗi tách thành mã; `DayDrafts` bỏ phần tử không phải lịch. ⚠️ Còn lại: **thiếu hẳn** một trường bắt buộc vẫn hỏng cả lô (function calling không ép đủ trường) | `PlanDraft`, `DayDrafts` |
| Chuẩn hoá trước bước sau | ✅ bỏ mã lạ / lặp, đổi id, gắn nhãn "Planner chưa sửa hết lỗi: …" | `xep_lich` |

## 2. Luồng gọi

| | Kết quả |
|---|---|
| `xep_lich` + prompt | ✅ `PLANNER` (luật 1–10) + `planner_input` |
| Model | ✅ `openai:gpt-4.1-mini` (settings), không thinking; trần đầu ra 2.500 + 500 × số ngày token |
| `DAYS_PER_PLANNER_CALL` = 4 | ✅ chia lô 4 ngày |
| Số lượt | ✅ ⌈số ngày ÷ 4⌉ mỗi nhánh × 3 nhánh (chuyến ≤ 4 ngày: 3 lượt) |
| 3 nhánh song song | ✅ `asyncio.gather` trong `all_branches` (coordinator/branches.py) |
| Nhánh không trả đủ | ✅ nhánh lỗi chạy lại 1 lần, vẫn lỗi thì bỏ nhánh; ngày thiếu lịch → `fallback_plans` |
| Ghép, đặt id | ✅ `N{ngày}-{id}` → `pick_option` thêm `V{nhánh}-` |

## 3. Danh sách chỗ đầu vào

| | Kết quả |
|---|---|
| `chot_danh_sach` | ✅ xem [rules-README.md](rules-README.md) mục 10 |
| Ưu tiên chỗ user chọn / nhắc | ✅ giữ chắc (`kept`), đứng đầu, không qua chia tầng |
| `pick_pool` | ✅ chia tầng không bị trội 5 mặt: sao tin được, số review, hợp mục đích, giá, gần trung tâm |
| `type_cap` | ✅ mỗi loại tối đa số ngày (spa × 0,5) |
| Mã D / Q | ✅ theo số review giảm dần; chỗ mượn nhánh khác đặt sau |
| Chỗ mượn nhánh khác | ✅ chỉ khi thiếu; không làm mỏ neo ngày |
| `split_by_day` | ✅ mỏ neo + cụm gần + giờ mở cửa giao khung ngày + mỗi ngày 1 chỗ mỗi loại |
| Chỗ bắt buộc xuyên suốt | ✅ giữ ở pool, vào đúng ngày ghim, `required` truyền vào `plan_errors` |
| Chỗ theo ngày vào Planner | ✅ mỗi ngày chỉ liệt kê chỗ của ngày đó (`day_codes`) |

## 4. Thời gian, tính khả thi

| | Kết quả |
|---|---|
| Giờ bắt đầu / kết thúc / rảnh | ✅ dòng `# Ngày …: 08:00–23:30, rảnh N phút, bữa bắt buộc …` |
| Không rời chỗ ở sớm | ✅ prompt luật 2 + `plan_errors` + `check_plan` (lỗi cứng) |
| Giờ mở cửa | ✅ từng chỗ, kể cả giờ nghỉ giữa ngày ("07:00–11:00 + 13:00–17:00", "đóng cửa", "chưa rõ") |
| Đóng cửa / sai giờ | ✅ chạy thử: chờ mở cửa > `LONG_WAIT_MAX` 45 phút, ở quá giờ đóng cửa → lỗi |
| Thời lượng, phút đi | ✅ "ở lại a–b phút", "phút từ chỗ ở", bảng phút đi (`PAIRS_PER_PLACE` 4 chỗ gần nhất) |
| Ma trận | ✅ `MAX_MATRIX_POINTS` 45, chia ô 12 |
| Thiếu ma trận | ✅ ước đường chim bay × 1,4 ở 25 km/h + 3 phút đỗ xe (ghi chú "ước") |
| Chạy thử ngắn / dài | ✅ ở ít (`stay_low`) / ở lâu (`stay_high`) |
| `ok / risky / warn / fail` | ✅ fail = lỗi khi ở ít · risky = lỗi khi ở lâu · warn = vượt "Tiêu khoảng" · ok |
| Buổi tối, spa 13:00 | ✅ chỗ "đi buổi tối" ưu tiên sau bữa tối (`evening_after_dinner`); spa coi như mở từ 13:00 (`AFTERNOON_FROM`) — ⚠️ bản nháp nói "nếu quy tắc hệ thống yêu cầu" (model không biết) |

## 5. Số lượng, độ khác biệt

| | Kết quả |
|---|---|
| `MIN_PLANS_PER_DAY` = 2 | ✅ kiểm trong `plan_errors`, không ngoại lệ; chỉ ghi nhãn |
| `DISTINCT_POOL` = 3 | ✅ ngày có ≥ 3 chỗ chơi (không tính chỗ bắt buộc) thì 2 lịch không được cùng bộ chỗ chơi D… |
| Nhận diện trùng | ✅ "trùng hệt" = cùng chuỗi `stops`; "cùng bộ chỗ chơi" = cùng tập D… |
| Chỉ khác thứ tự | ✅ coi là trùng (khi ngày đủ chỗ) — ghi lỗi, **không loại** lịch |
| Thiếu chỗ → trùng một phần | ✅ chấp nhận; bước ghép chỉ ưu tiên ít trùng |
| `fallback_plans` | ✅ ngày không có lịch (Planner lỗi / thiếu ngày / mọi mã sai) → 1 lịch, nhãn "lịch dự phòng" |
| Fallback < 2 lịch | ✅ có (1 lịch); bước ghép vẫn chạy, 3 phương án giống nhau ở ngày đó |
| Loại vì trùng một phần | ✅ không loại |

## 6. Chỗ bắt buộc

| | Kết quả |
|---|---|
| Nhãn | ✅ `| BẮT BUỘC` + `| user muốn tới <giờ / buổi>` — ⚠️ bản nháp không nhắc đúng chữ này |
| `plan_errors` | ✅ "thiếu chỗ BẮT BUỘC" |
| Mọi phương án giữ | ✅ mọi lịch của ngày ghim phải có; `check_plan` fail nếu thiếu |
| Đóng cửa / không xếp được | ✅ xem kết luận 4 |
| Thứ user nhắc | ✅ `| user nhắc: …`, giữ qua hậu xử lý (trim / drop bỏ qua `wanted`) |
| Hậu xử lý có xoá bắt buộc? | ✅ không: `drop_same_group`, `drop_unfit_afternoon`, `trim_visits`, `drop_*_meals` đều bỏ qua `must`; `reorder_visits` giữ tập chỗ; `fill_visits` chỉ thêm |

## 7. Bữa ăn, nghỉ trưa

| | Kết quả |
|---|---|
| Bữa bắt buộc | ✅ `required_meals` theo giờ bắt đầu ngày, ghi sẵn ở dòng ngày |
| Chọn quán | ✅ Planner chọn quán Q… trong danh sách ngày; code đặt lại đúng giờ |
| 1 quán mỗi bữa | ✅ `drop_extra_meals` |
| Không 2 quán liền | ✅ prompt luật 6 + `fill_visits` (bù chỗ chơi vào giữa 2 bữa liền nhau) |
| Quán chỉ bữa sáng | ✅ `breakfast_only` → bỏ ở trưa / tối |
| Món user nhắc 1 bữa / ngày | ✅ prompt luật 9 + `drop_extra_meals` |
| Thứ tự | ✅ `place_meals` → `drop_extra_meals` (+ đặt lại) → `drop_bad_meals` (+ đặt lại + lọc lại) → `drop_extra_meals`; sau bù chỗ lọc lại 1 lượt |
| Quán dự phòng | ✅ tối đa `MAX_SPARE_MEALS` 4 quán của ngày, không cùng chuỗi, không trùng món user nhắc |
| `MIN_MEAL_GAP` 150 | ✅ `meal_time_issues` (lỗi) + `drop_bad_meals` (bỏ quán quá sát) |
| `NAP_MIN` 90 | ✅ chạy thử tự chèn sau bữa trưa (ngày bắt đầu ≤ 09:00, kết thúc ≥ 17:00) — ⚠️ bản nháp "dành khoảng nghỉ trưa" dễ làm Planner chừa trống |
| 45 / 150 phút | ✅ chờ mở cửa / chờ giờ ăn > 45 là lỗi; bữa tối là điểm cuối được trống tới 150 |

## 8. Ngân sách

| | Kết quả |
|---|---|
| `day_target` | ✅ = `day_allowance` = (mốc ÷ 1,125 − tiền phòng) ÷ số ngày, cả đoàn, vé + ăn + đi lại |
| Quan hệ với `budget_band` | ✅ `day_allowance` chia từ mốc (`trip_budget`); `budget_band` dùng ở bước ghép |
| Chi phí từng lịch | ✅ `metrics.spend` (`day_spend`), `metrics.cost` (vé + ăn) |
| Thiếu giá | ✅ `fill_price_estimates` (ước, ghi rõ) + Agent Budget + tính lại sau Agent Budget |
| Planner tự tính ngân sách? | ✅ không — chỉ nhận "Tiêu khoảng X" |
| Thấp / sát / cao | ✅ luật 8 (prompt hiện tại) — bản nháp giống ý |
| Mọi lịch vượt | ✅ không loại; bước ghép chọn tổ hợp lệch ít nhất; vượt mức trên → `ask_user` |
| Loại vì vượt dù đủ bắt buộc? | ✅ không |
| `warn` khớp prompt | ✅ |

## 9. Sửa, hậu xử lý

| | Kết quả |
|---|---|
| `PLANNER_FIXES = False` | ✅ tắt cả lượt sửa ở `xep_lich` và ở `kiem_lich` |
| `plan_errors` | ✅ chỉ ghi nhận (nhãn), không sửa, không loại |
| Thứ tự `kiem_lich` | ✅ cùng loại / spa → sắp thứ tự → đặt quán → bỏ quán → bù chỗ → lọc lại quán → bỏ bớt → chạy thử, gắn trạng thái |
| `reorder_visits` | ✅ có đổi thứ tự Planner chọn (cho ít ngồi xe), chỉ nhận khi không thêm lỗi |
| `fill_visits` | ✅ xem mục 7; không đụng chỗ bắt buộc |
| `MAX_TRIMS` = 3 | ✅ |
| Không khắc phục được | ✅ giữ lịch, trạng thái fail / risky, lỗi ghi trong `issues` |
| Cảnh báo tới kết quả cuối | ✅ `issues`, `unchecked`, `meal_notes`, notes hiện trên FE |

## 10. Kiểm thử A/B — đã chạy 16/10/2026

**Cách làm.**
- Lấy state đã lưu của 5 chuyến:
  - Hội An 3 ngày.
  - Đà Nẵng 3 ngày.
  - Huế 3 ngày, gia đình 4 người, 3 chỗ bắt buộc (2 chỗ có giờ hẹn).
  - Phú Quốc 3 ngày, ngân sách 3 triệu.
  - Đà Lạt 5 ngày (2 lô Planner).
- Giữ nguyên danh sách chỗ, mã, chia ngày, ma trận. Chỉ gọi lại Planner (`gpt-4.1-mini`), rồi chạy `kiem_lich` bằng code.
- So 3 bản prompt:
  - **cũ**: luật 5 chỉ có "ít nhất 2 lịch, không trùng hệt".
  - **mới**: prompt hiện tại, luật 5 thêm mã lịch không trùng, không 2 chỗ chơi cùng loại, ưu tiên "hợp mục đích".
  - **nháp**: nguyên bản nháp.
- Số lượt: 5 ca × 3 nhánh × 3 bản = 45 lượt; mỗi bản 15 lượt, 1 lần lặp. Tổng chi phí $0,11.

| Chỉ số | cũ | mới | nháp |
|---|---|---|---|
| Output Planner hợp lệ / ngày phải dùng lịch dự phòng | 100% / 0 | 100% / 0 | 100% / 0 |
| Lịch mỗi ngày | 2,02 | 2,00 | 2,00 |
| **Lỗi luật (tổng)** | 21 | **8** | 12 |
| – 2 lịch cùng bộ chỗ chơi | 13 | 4 | 6 |
| – 2 chỗ chơi cùng loại trong lịch | 7 | 2 | 4 |
| – dùng mã của ngày khác | 0 | 0 | 2 |
| – thiếu chỗ bắt buộc | 1 | 2 | 0 |
| Khác biệt chỗ chơi giữa các lịch cùng ngày (0–1) | 0,34 | **0,53** | 0,44 |
| Số chỗ chơi code phải bỏ | 25 | **11** | 23 |
| Số quán code phải đổi | 121 | 117 | 137 |
| Code thêm chỗ chơi | 32 | 34 | 34 |
| Lịch qua kiểm giờ (ok + warn) | 11/103 | 21/102 | 16/102 |
| Lịch fail | 43/103 (42%) | 45/102 (44%) | 43/102 (42%) |
| Giữ chỗ bắt buộc ở lịch cuối | 92% | 92% | 100% |
| Token vào, trung bình mỗi nhánh | 4.041 | 4.137 | 6.413 |
| Chi phí 15 lượt | $0,032 | $0,032 | $0,046 |

**So từng cặp (cùng ca, cùng nhánh), theo số lỗi luật:**
- Mới so với cũ: ít lỗi hơn ở 7 cặp, hoà 8, không cặp nào nhiều hơn. Kiểm định dấu (sign test) p ≈ 0,016.
- Nháp so với cũ: ít hơn 5, hoà 9, nhiều hơn 1 (p ≈ 0,22).
- Mới so với nháp: ít hơn 5, hoà 9, nhiều hơn 1 (p ≈ 0,22).

**Đọc kết quả:**
- **Mới tốt hơn cũ rõ ràng và không tốn thêm tiền.**
  - Lỗi luật giảm 62%.
  - Các lịch cùng ngày khác nhau nhiều hơn.
  - Code bỏ ít chỗ hơn (11 so với 25), vì Planner không còn xếp 2 chỗ cùng loại.
- **Không thay bằng bản nháp.**
  - Không hơn bản mới.
  - Tốn hơn 44% token.
  - Có lỗi mới: dùng mã của ngày khác.
  - Code phải đổi quán nhiều nhất.
  - Điểm hơn duy nhất là giữ chỗ bắt buộc 100% so với 92%, nhưng chỉ chênh 1–2 lịch. Nên để code tự chèn chỗ bắt buộc bị thiếu thay vì đổi prompt.
- **Giới hạn của lần chạy này:**
  - Mỗi ô chỉ chạy 1 lần.
  - Phần lớn chênh lệch ok / warn đến từ 1 ca (Phú Quốc: bản mới 11/12 lịch ngày 1–2 qua kiểm giờ, bản cũ 3/12).
  - Thời gian Planner không so được, vì `kiem_lich` chặn event loop khi 9 lượt chạy song song.
- **Tỉ lệ fail khoảng 42% như nhau ở cả 3 bản**, gần như giống hệt theo từng cặp. Fail không do prompt, xem nguyên nhân ngay dưới.

### Nguyên nhân fail (code / dữ liệu, không do prompt)

Tìm bằng cách đọc lại lịch đã lưu và chạy thử lại bằng code trên state, không gọi LLM.

1. **Chỗ hẹn giờ đẩy bữa ăn tới tối** (Huế: 12/18 lịch fail, ngày 1–2).
   - Nguyên nhân: `simulate` cho quán đứng trước một chỗ hẹn giờ được chờ tối đa 24 tiếng. Vòng chờ lại đi qua cả 3 mốc giờ ăn.
   - Ví dụ: quán sáng tới 08:05 bị đẩy tới 18:00 (chờ 595 phút), vì cuối ngày có Chợ Đông Ba 16:30. Ngày 2 có Đại Nội "buổi sáng" cũng bị như vậy.
   - Hậu quả: không đặt được bữa sáng / bữa trưa trước chỗ hẹn, nên lịch thiếu bữa và "bữa sáng phải là việc đầu tiên".
   - Hướng sửa: chỉ chờ tới mốc giờ ăn **kế tiếp**; đã ở trong khung giờ ăn thì chờ tối đa `MEAL_WAIT_MAX`.
2. **Chuyến dài thiếu chỗ chơi** (Đà Lạt 5 ngày: 16–18/30 lịch fail, phần lớn "thiếu bữa tối").
   - Nguyên nhân 1: `chot_danh_sach` (chạy riêng mỗi nhánh) lấy quán trước trong 45 điểm của ma trận (31 quán cho 14 bữa × 2 lựa chọn + 3). Chỗ chơi chỉ còn 13 điểm. Sau giới hạn mỗi loại, mỗi nhánh còn 7–11 chỗ chơi cho 5 ngày, tức 1–3 chỗ mỗi ngày.
   - Hậu quả: ngày chỉ có 1 chỗ chơi thì buổi chiều trống quá 150 phút, không xếp được bữa tối.
   - Chuyến 7 ngày: 43 quán (20 bữa × 2 + 3), chỉ còn 1 điểm cho chỗ chơi (nếu điểm đến có đủ quán).
   - Nguyên nhân 2: loại chung "điểm thu hút khách du lịch" của Google bị coi là một loại (Cung Nam Phương Hoàng Hậu "cùng loại" với Rừng Giao Hưởng). Vì vậy cả chuyến lấy tối đa 5 chỗ loại này, và mỗi ngày / mỗi lịch chỉ được 1 chỗ.
   - Hướng sửa: chia 44 điểm của ma trận theo nhu cầu chỗ chơi / quán. Loại chung chung thì không tính là một loại (xét tên như khi loại trống).
3. **Ngày cuối về 12:30 không kịp ăn trưa** (Phú Quốc: 6/6 lịch ngày 3 fail; các ca khác ngày cuối đều risky).
   - Nguyên nhân: tới quán trưa lúc 10:40, đã trong khung trưa 10:30–14:00, mà vẫn chờ tới mốc 11:30. Quán cách chỗ ở 20–46 phút nên về lúc 12:38–13:25.
   - Hướng sửa: không chờ tới mốc giờ ăn nếu chờ xong không kịp về trước giờ kết thúc ngày.
4. **Không gây fail trực tiếp nhưng làm lịch xấu:** chuyến Đà Nẵng có 21/55 chỗ ở Hội An (cách 24 km), vì MCP gộp Hội An vào Đà Nẵng. Nhiều lịch đi qua lại giữa 2 nơi trong một ngày.

Lần A/B sau nên lưu output thô của Planner. Khi đó sửa code xong có thể chạy lại `kiem_lich` trên đúng các output đó mà không tốn tiền.

### Đã sửa (16/10/2026)

| Lỗi | Sửa | Ở đâu |
|---|---|---|
| 1. Chỗ hẹn giờ đẩy bữa ăn | Chỉ chờ tới mốc giờ ăn **kế tiếp**. Đang trong khung của bữa chưa ăn thì ăn luôn, chỉ chờ mốc của chính bữa đó. `drop_bad_meals` không bỏ quán chờ lâu khi còn chỗ hẹn giờ phía sau | `simulate`, `drop_bad_meals` |
| 2. Chuyến dài thiếu chỗ chơi | Ma trận chia điểm cho chỗ chơi / quán theo tỉ lệ nhu cầu (`meal_points`, `visit_need`). Loại chung chung không tính là một loại (`Place.type_key`; Planner thấy "–", luật 5 ghi rõ không tính) | score.py, models.py, prompt |
| 3. Ngày cuối không kịp ăn trưa | Chờ tới mốc mà về chỗ ở quá giờ kết thúc ngày thì chỉ chờ cho đủ cách bữa trước `MIN_MEAL_GAP` | `simulate` |
| 4. Trộn Đà Nẵng / Hội An trong một ngày | Chỗ chơi và quán vào ngày có chỗ chơi cách ≤ `NEAR_MIN` 30 phút trước, kể cả khi ngày đó đã có chỗ cùng loại; ngày gần đã đầy mới sang ngày xa (giữ trần mỗi ngày). Bản đầu "chỉ ngày gần" làm ngày Bà Nà / Lăng Cô thiếu chỗ (Đà Nẵng fail 4 → 9) nên đã bỏ | `split_by_day` |
| Thiếu chỗ bắt buộc (A/B: 92%) | Code chèn chỗ user chọn mà Planner bỏ sót vào chỗ ít lỗi nhất | `add_missing_musts` |

Chạy lại `kiem_lich` (code mới) trên lịch đã lưu của 5 chuyến. Chưa tính phần chọn chỗ / chia ngày mới vì lịch đã lưu dùng danh sách cũ:

| Chuyến | fail trước → sau |
|---|---|
| Huế | 12 → 0 |
| Phú Quốc | 6 → 3 |
| Hội An | 3 → 1 |
| Đà Nẵng | 4 → 4 |
| Đà Lạt | 16 → 16 |
| **Tổng** | **41 → 24** |
