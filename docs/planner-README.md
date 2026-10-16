# Agent Planner — xếp lịch từng ngày

Planner là agent LLM duy nhất **xếp lịch** trong pipeline: nhận danh sách chỗ đã chia sẵn cho từng
ngày, trả vài lịch đi khác nhau mỗi ngày. Planner **không chấm** lịch nào hay hơn — code chạy thử
theo đồng hồ, tự sửa, rồi xếp hạng và ghép phương án. Đường dẫn tính từ `ailusion-journey-llm/`,
dẫn theo tên hàm / hằng số.

Code chính: [agents/planner.py](../src/ailusion/itinerary/agents/planner.py) (`xep_lich`).
Model: `openai:gpt-4.1-mini`, không bật thinking.

## Vị trí trong pipeline

```
chot_nhanh (mỗi nhánh)                     lap_phuong_an (mỗi nhánh, chạy song song ×3)
  chot_danh_sach ─► pool + mã D/Q            ┌─────────────────────────────────────────┐
                   + chia ngày               │ xep_lich (PLANNER)  → lịch mỗi ngày      │
                   + ma trận phút đi   ───►  │ kiem_lich (code)    → sửa, chạy thử, gắn │
                                             │                       trạng thái         │
                                             │ xep_hang (code)     → xếp hạng           │
                                             └─────────────────────────────────────────┘
                                                        │
                                             pick_option → PA1 / PA2 / PA3
```

- 3 nhánh song song ([coordinator/branches.py](../src/ailusion/itinerary/coordinator/branches.py)), mỗi nhánh 1 lượt Planner (≤ 4 ngày một lượt) → thường **3 lượt Planner** mỗi lộ trình.
- Mỗi nhánh có danh sách chỗ chơi riêng (`split_shares`), chỗ của nhánh khác chỉ mượn khi thiếu, quán ăn dùng chung.

## Vào: Planner nhận gì (`planner_input`)

Bước `chot_danh_sach` ([coordinator/score.py](../src/ailusion/itinerary/coordinator/score.py)) chuẩn bị trước:

| | Cách làm |
|---|---|
| Danh sách chỗ (`pool`) | Chỗ user chọn + thứ user nhắc, rồi chia tầng không bị trội (`pick_pool`: sao tin được, số review, hợp mục đích, giá, khoảng cách); mỗi loại tối đa `type_cap` chỗ (spa thưa hơn); loại Google chung chung ("Điểm thu hút khách du lịch") không tính là một loại |
| Mã | `D1, D2…` chỗ chơi, `Q1, Q2…` quán — đặt theo số review giảm dần (Planner hay chọn mã đầu), chỗ mượn của nhánh khác đặt sau |
| Chia ngày (`split_by_day`) | Mỗi ngày 1 "mỏ neo" (chỗ nổi tiếng nhất còn mở, ưu tiên chỗ của nhánh), các chỗ khác vào ngày gần cụm nhất; giờ mở cửa phải giao khung ngày đủ thời gian ở; mỗi ngày tối đa 1 chỗ mỗi loại; chỗ (cả quán) vào ngày có chỗ chơi cách ≤ `NEAR_MIN` 30 phút trước, ngày gần đã đầy mới sang ngày xa — không trộn 2 vùng khi không cần |
| Phút đi | Ma trận `distance_matrix` (tối đa `MAX_MATRIX_POINTS` = 45 điểm, không đủ thì chia cho chỗ chơi / quán theo tỉ lệ nhu cầu); thiếu cặp thì ước theo đường chim bay |

Văn bản đưa Planner, mỗi ngày:

```
# Ngày 1 (23/10): 08:00–23:30, rảnh 840 phút, bữa bắt buộc: sáng, trưa, tối; nghỉ trưa 90 phút
  tại chỗ ở hoặc quán cà phê gần quán trưa sau bữa trưa (hệ thống tự chèn); <thời tiết>
Chỗ dùng được ngày này (mã | tên | loại | giờ mở cửa | ở lại | giá/người | phút từ chỗ ở):
D1 | Chùa Cầu | điểm mốc lịch sử | 09:00–22:00 | 30–60 phút | 0 đ | 7 phút | hợp mục đích
Q3 | Bún bò … | nhà hàng | 06:00–14:00 | 45–90 phút | 50.000 đ | 5 phút | user nhắc: bún bò
…
Phút đi giữa các chỗ của ngày 1: D1-D2 6, D1-Q3 4, …   (mỗi chỗ chỉ PAIRS_PER_PLACE = 4 chỗ gần nhất)
```

Cuối bài: nhóm khách + mục đích; nhu cầu bắt buộc user tự ghi (`needs_note`); `# Tiêu khoảng X đ mỗi ngày cho cả đoàn` (`day_target` = `day_allowance`, xem [budget-README.md](budget-README.md)).

Nhãn trên từng chỗ: `hợp mục đích` · `đi buổi tối` (mở từ chiều muộn) · `BẮT BUỘC` (+ giờ user muốn tới) · `user nhắc: …`.

## Luật trong prompt (`PLANNER`)

**Luật**

| # | Luật |
|---|---|
| 1 | Chỉ dùng mã trong danh sách, không bịa chỗ |
| 2 | `leave_stay` không sớm hơn giờ bắt đầu ngày |
| 3 | Cả lịch (kể cả ở lại, đi về) nằm trong giờ rảnh; không chạm khung bị chặn; khung "chỉ ăn uống" chỉ xếp quán |
| 4 | Không xếp chỗ đóng cửa; tới trong giờ mở cửa |
| 5 | Mỗi ngày ≥ 2 lịch, không trùng hệt; mã lịch (L1, L2…) không trùng trong ngày; mỗi lịch không 2 chỗ chơi cùng loại (cột thứ 3; loại "–" là chưa rõ loại, không tính); ưu tiên chỗ "hợp mục đích" |
| 6 | Bữa ăn: mỗi bữa bắt buộc của ngày (tính sẵn theo giờ bắt đầu ngày) 1 quán, đặt gần đúng chỗ — **code tự dời quán vào đúng giờ ăn**, bỏ quán thừa / sai giờ; không 2 quán liền nhau; mọi chỗ bán đồ ăn tính 1 bữa; quán "bữa sáng" chỉ làm bữa sáng |
| 7 | Ngày kéo tới tối có chỗ chơi sáng **và** chiều; ngày dài ít nhất `MIN_VISITS` (2) chỗ chơi; đừng nhồi tới mức về trễ (code bỏ bớt); spa từ 13:00; chỗ "đi buổi tối" xếp sau bữa tối |
| 8 | Các lịch trong ngày có mức tiền khác nhau quanh "Tiêu khoảng X": 1 lịch thấp hơn, 1 lịch cao hơn chút (lịch thứ 3 nếu có thì sát) |
| 9 | Thứ "user nhắc" có ít nhất 1 lần mỗi phương án, mỗi ngày tối đa 1 bữa cùng món |
| 10 | Ngày "nghỉ trưa": hệ thống tự chèn `NAP_MIN` (90) phút; chiều phải có chỗ chơi; chờ quá `LONG_WAIT_MAX` (45) phút là lỗi, riêng bữa tối cuối ngày được trống tới `DINNER_FREE_MAX` (150) phút |

Các số trong prompt điền từ hằng số chung ở [shared/form.py](../src/ailusion/itinerary/shared/form.py) (`MIN_VISITS`, `NAP_MIN`, `LONG_WAIT_MAX`, `DINNER_FREE_MAX`) — cùng số bước chạy thử kiểm.

**Làm lịch khác nhau thật sự**: khác kiểu (ít ngồi xe / nhiều chỗ hợp ý / chỗ đánh giá cao), khác **chỗ chơi** (không cùng bộ D…, mỗi lịch ≥ 1/2 chỗ chơi khác lịch kia — `DISTINCT_POOL` = 3: ngày có ≥ 3 chỗ chơi mới bắt).

## Ra: Planner trả gì (`PlannerOutput`)

```json
{"days": [{"day": 1, "plans": [{"id": "L1", "leave_stay": "08:00", "stops": ["Q3", "D1", "D4", "Q7", "D2", "Q9"]}]}]}
```

Mã lịch đổi thành `N{ngày}-{id}` (sau ghép nhánh thành `V{nhánh}-N1-L1`).

## Code kiểm ngay sau Planner (`plan_errors`)

Mã ngoài danh sách · mã không thuộc ngày đó · rời chỗ ở sớm · < `MIN_PLANS_PER_DAY` (= 2) lịch · 2 lịch trùng · mã lặp trong 1 lịch (giữ lần đầu) · 2 chỗ chơi cùng loại trong 1 lịch · thiếu chỗ BẮT BUỘC.

- Lượt Planner sửa (`PLANNER_FIX`) **đang tắt** (`PLANNER_FIXES = False`): tốn ~2/3 token cả lộ trình mà code đã tự sửa phần lớn. Lịch còn lỗi được giữ, gắn nhãn "Planner chưa sửa hết lỗi: …".
- Ngày Planner không trả lịch nào → `fallback_plans`: code ghép 1 lịch (2 chỗ chơi khác loại + 1 quán), gắn nhãn "lịch dự phòng".

## Code sửa lịch sau Planner (`kiem_lich`, [coordinator/simulate.py](../src/ailusion/itinerary/coordinator/simulate.py))

Theo thứ tự:

1. `drop_same_group`, `drop_unfit_afternoon` — mỗi ngày 1 chỗ mỗi loại; spa / massage trong ngày kết thúc trước 13:00 thì bỏ. `add_missing_musts` — chỗ user chọn mà Planner bỏ sót: chèn vào chỗ ít lỗi nhất.
2. `reorder_visits` — sắp lại thứ tự chỗ chơi cho ít ngồi xe (Planner hay chạy qua lại).
3. `place_meals` — đặt quán vào đúng giờ ăn (thử mọi chỗ chèn, thêm quán dự phòng khi thiếu bữa, bỏ bớt quán thừa). Ưu tiên: ít lỗi giờ ăn → ít lỗi khác → chỗ buổi tối đi sau bữa tối (`evening_after_dinner`) → ít bỏ quán Planner chọn → ít thêm quán → ít bữa cùng loại món → về sớm.
4. `drop_extra_meals`, `drop_bad_meals` — mỗi khung giờ ăn 1 quán, mỗi món user nhắc 1 bữa/ngày; bỏ quán ngoài giờ ăn, chờ mở cửa lâu, quá sát bữa trước (< 150 phút); đặt lại quán.
5. `fill_visits` — bù chỗ chơi khi: ngày dài còn < 2 chỗ chơi (ngày ngắn < 1), lịch xong sớm mà còn bữa tối, 2 bữa liền nhau, hoặc phải chờ lâu trước một chỗ chơi (chờ chỗ buổi tối mở). Chỉ lấy chỗ cách ≤ 30 phút, khác loại, **không có trong lịch ngày khác**; chỉ nhận khi không thêm lỗi.
6. `trim_visits` — lịch quá dài (về trễ, chờ lâu, ở quá giờ đóng cửa) bỏ bớt tối đa 3 chỗ chơi, ghi "Bỏ X: không đủ thời gian trong ngày".
7. `check_plan` — chạy thử 2 lần (ở ít / ở lâu), gắn trạng thái:

| Trạng thái | Khi nào |
|---|---|
| `fail` | Có lỗi khi ở ít nhất (thiếu bữa, quá giờ, đóng cửa, thiếu chỗ ngủ, thiếu chỗ user chọn…) |
| `risky` | Ở ít thì ổn, ở lâu thì lỗi ("Sát giờ nếu ở lâu") |
| `warn` | Vượt "Tiêu khoảng X" mỗi ngày |
| `ok` | Không lỗi |

Nghỉ trưa (`NAP_MIN` = 90 phút) do bước chạy thử tự chèn sau bữa trưa ở ngày dài: về chỗ ở, quán cà phê hoặc khách sạn gần quán trưa (`rest_spot`).

## Hằng số

| Số | Giá trị | Ở đâu |
|---|---|---|
| `DAYS_PER_PLANNER_CALL` | 4 ngày / lượt | planner.py |
| `MIN_PLANS_PER_DAY` | 2 lịch / ngày | planner.py |
| `PLANNER_FIXES` | tắt | planner.py |
| `DISTINCT_POOL` | 3 chỗ chơi | planner.py |
| `PAIRS_PER_PLACE` | 4 chỗ gần nhất | planner.py |
| `MAX_PAIRS_PER_DAY` | 300 cặp | planner.py |
| `MAX_MATRIX_POINTS` | 45 điểm | score.py |
| `NAP_MIN` | 90 phút | shared/form.py |
| `MIN_MEAL_GAP` | 150 phút | shared/form.py |
| `LONG_WAIT_MAX` / `DINNER_FREE_MAX` | 45 / 150 phút | shared/form.py |
| `NEAR_MIN` | 30 phút (bù chỗ, chia ngày) | shared/form.py |
| `MAX_FILLS` | 3 | simulate.py |
| `MEAL_WAIT_MAX` | 45 phút | simulate.py |
| `SPARE_VISITS` | 2 chỗ dư / ngày | score.py |
| `MIN_VISITS` / `FULL_DAY_MIN` | 2 chỗ / 360 phút | shared/form.py |
| `MAX_TRIMS` | 3 chỗ | simulate.py |

## Đã chỉnh cho khớp code (10/2026)

- Luật đánh số liền 1–10 (trước đó nhảy từ 6 sang 11–13).
- Luật nghỉ trưa nói đúng: chờ quá 45 phút là lỗi, riêng bữa tối cuối ngày được trống tới 150 phút.
- Bỏ con số "4–6 chỗ chơi" (code không có trần): nói đúng mức tối thiểu code bù (`MIN_VISITS`) và "quá dài code bỏ bớt".
- Luật tiền khớp 2 lịch mỗi ngày (`MIN_PLANS_PER_DAY` = 2): 1 thấp, 1 cao hơn chút, lịch thứ 3 (nếu có) sát.
- Luật bữa ăn rút gọn: bỏ phần code đã tự làm (khung giờ trưa / tối, 2 bữa cách 2,5 tiếng) — Planner chỉ cần chọn đủ quán, đặt gần đúng chỗ.
- `LONG_WAIT_MAX`, `DINNER_FREE_MAX`, `MIN_VISITS`, `FULL_DAY_MIN` chuyển sang shared/form.py để prompt và code cùng một số.
- Luật 5 thêm: mã lịch không trùng trong ngày; không 2 chỗ chơi cùng loại trong một lịch (loại "–" không tính); ưu tiên "hợp mục đích". A/B 5 chuyến: lỗi luật 21 → 8 (xem [planner-prompt-review.md](planner-prompt-review.md) mục 10).
- Output sai kiểu một trường không còn làm mất cả lô; mã lịch trùng không còn ghi đè nhau.
- Chờ giờ ăn chỉ tới mốc kế tiếp; chỗ hẹn giờ không kéo bữa sáng tới bữa tối; ngày cuối ăn trưa sớm cho kịp về. Chỗ user chọn bị bỏ sót thì code chèn. Chia ngày xét ngày gần trước (không trộn 2 vùng khi không cần). Ma trận chia điểm theo nhu cầu (chuyến dài đủ chỗ chơi). Tên chỗ bắt buộc không tính chữ của tên điểm đến khi so khớp.

## Còn để ý

- **Lượt sửa tắt** → lỗi sai luật (mã lạ, lịch trùng) chỉ được code dọn được phần nào; phần còn lại thành nhãn "Planner chưa sửa hết lỗi".
- **Show / chỗ buổi tối**: không có giờ diễn trong DB; code chỉ ưu tiên đi sau bữa tối, buổi chiều không đủ chỗ thì vẫn xếp trước bữa tối.
