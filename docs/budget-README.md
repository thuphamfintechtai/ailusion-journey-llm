# Quản lý ngân sách — lộ trình đi qua những bước nào

Ngân sách user nhập đi qua từng bước của pipeline lập lộ trình ra sao, mỗi bước dùng số nào (lấy từ
dữ liệu hay **tự đặt**), và còn gì phải để ý. Đường dẫn tính từ thư mục `ailusion-journey-llm/`
(trừ phần FE). Dẫn theo tên hàm / hằng số (số dòng đổi khi sửa code).

## Tóm tắt luồng

```
Form (FE) / chat / câu hỏi H3
        │
        ▼
Khoảng ngân sách cả chuyến (budget_band)  +  mốc (trip_budget)
        │
        ├─► Tìm chỗ ở quanh giá mục tiêu, chọn chỗ ở ≈ 35% mốc (sang: 50%)
        ├─► Giá vé: DB → Agent Budget → ước (ghi rõ "ước")
        │
        ▼
Planner: "tiêu khoảng X mỗi ngày"  (day_allowance)
        │
        ▼
Chạy thử từng lịch: tiền cả ngày = vé + ăn + đi lại (day_spend) — so với X
        │
        ▼
Ghép 3 phương án: tổng ước nằm trong khoảng ngân sách
        │
        ├─ vượt mức trên  → hỏi user (giữ lịch / đổi lịch rẻ nhất mỗi ngày)
        ├─ thấp hơn mức dưới → ghi chú + gợi ý nâng chỗ ở, thêm hoạt động có phí
        └─ luôn: % tổng ước là giá ước; ngân sách dưới mức chi tối thiểu của điểm đến
```

---

## 1. Nhập ngân sách

| Ở đâu | Làm gì |
|---|---|
| Form FE ([trip-form-sheet.tsx](../../ailusion-journey-fe/src/components/itinerary/trip-form-sheet.tsx)) | Chip 3 / 5 / 8 / 12 / 20 / 30 triệu + ô gõ tuỳ ý (`BUDGETS`) |
| `budget_unit` | `group_trip` (cả đoàn cả chuyến, mặc định) · `person_trip` (mỗi người cả chuyến) · `person_day` (mỗi người mỗi ngày) |
| `spend_style` | `normal` (±5%) · `luxury_within_budget` — FE ghi "Sang, không vượt ngân sách" · `unlimited` (bỏ mọi luật tiền) |
| `budget_min` | Mức dưới khi user chọn **khoảng** ở câu hỏi H3. FE tự xoá khi user nhập lại ngân sách / đổi cách tính (`set` trong trip-form-sheet) |
| `prepaid_tickets` | Tiền vé đi về đã mua, mỗi người — trừ khỏi ngân sách |
| Chat ([chat/trip_info.py](../src/ailusion/chat/trip_info.py), [chat/prompts.py](../src/ailusion/chat/prompts.py)) | Tự đọc `budget_million` ("15tr", "2 triệu mỗi người") và `budget_hard` (có được vượt không). FE `groupBudget` đổi form về cả đoàn cả chuyến khi báo cho chat; `budget_hard = true` khi mức chi "Sang, không vượt" |
| Câu hỏi H3 (`questions_for`, [coordinator/profile.py](../src/ailusion/itinerary/coordinator/profile.py)) | Thiếu ngân sách thì hỏi, mỗi lựa chọn là 1 khoảng: Dưới 3tr = tối đa 3tr · 3–6tr · 6–10tr · Trên 10tr = từ 10tr, không trần |

## 2. Khoảng ngân sách và mốc ([shared/form.py](../src/ailusion/itinerary/shared/form.py))

Mọi số đều là **cả đoàn cả chuyến, đã trừ vé đi về mua trước**.

| Hàm | Ra gì |
|---|---|
| `budget_band(form)` | (thấp, cao) mà tổng ước phải nằm trong: nhập 1 số → ±`BUDGET_BAND` (5%, user yêu cầu); chọn khoảng → đúng khoảng (thiếu cao = không trần, thấp 0 = không sàn); "sang, không vượt" → (−5%, 0) |
| `trip_budget(form)` | **Mốc** để chia tiền phòng, tiền mỗi ngày: số user nhập; chọn khoảng thì giữa khoảng (không sàn thì mức trần, không trần thì mức sàn) |
| `daily_budget(form)` | Mốc ÷ số người ÷ số ngày (mỗi người mỗi ngày) — chỉ dùng để nói "giá nhẹ so với ngân sách" trong lý do chọn chỗ |
| `day_allowance(form, mốc, giá phòng)` | Tiền nên tiêu mỗi ngày cho cả đoàn (vé + ăn + đi lại): (mốc ÷ (1 + `EXTRA_MID`) − tiền phòng) ÷ số ngày. Planner nhận số này, bước chạy thử so lịch với số này |

Hằng số dùng chung, chỉ khai báo ở đây: `BUDGET_BAND = 0.05`, `EXTRA_PCT = (0.10, 0.15)` (phát sinh, tự đặt), `EXTRA_MID = 0.125`.

## 3. Nhóm khách ([coordinator/profile.py](../src/ailusion/itinerary/coordinator/profile.py))

- Luật B0 → B4 xếp nhóm P1–P4. Luật B3 cũ (ngân sách thấp → nhóm tiết kiệm) đã bỏ: xếp tiết kiệm thì cả chuyến tiêu thấp hơn ngân sách tới 35%. P1 chỉ còn khi user chọn ưu tiên "tiết kiệm".
- `base_daily_cost`: chi tối thiểu 1 người 1 ngày ở điểm đến = giá phòng phổ biến ÷ 2 + 3 × giá 1 bữa phổ biến (trung vị dữ liệu của điểm đến). Dùng ở bước ghép để báo ngân sách quá thấp (mục 8).

## 4. Chỗ ở — phần lớn nhất của ngân sách ([coordinator/score.py](../src/ailusion/itinerary/coordinator/score.py), [coordinator/search.py](../src/ailusion/itinerary/coordinator/search.py))

| Số | Giá trị | Nguồn |
|---|---|---|
| `STAY_TARGET_SHARE` | tiền phòng nhắm **35%** mốc | tự đặt |
| `STAY_BUDGET_SHARE` | trần **50%** mốc; "sang, không vượt" nhắm luôn mức này (`stay_target_share`) | tự đặt |
| `STAY_BAND` | tìm thêm phòng giá **60%–140%** mục tiêu / đêm / phòng (`stay_band_calls`) | tự đặt |
| `STAY_PRICE_PICKS` / `STAY_PRICE_SLACK` | lấy 8 chỗ giá sát mục tiêu, cho lệch thêm 25% để được chỗ gần các điểm chơi hơn | tự đặt |
| Số phòng | 2 người 1 phòng, làm tròn lên (`stay_cost`, rank.py) | tự đặt |

Cách chọn:
1. `usable_stays`: bỏ chỗ cách trung tâm > 30 km, bỏ chỗ tiền phòng cả chuyến > 50% mốc (`unlimited` thì không bỏ theo giá).
2. Mục tiêu tiền phòng = mốc × `stay_target_share` → 8 chỗ giá sát nhất → chỗ ít phút đi tới 12 điểm chơi nổi bật nhất.
3. User đã đặt chỗ ở (`stay_booked`) → tiền phòng 0.

## 5. Giá vé, giá ăn

1. **DB**: giá Google ghi (VD "100–200 N ₫" → số giữa).
2. **Agent Budget** ([agents/budget.py](../src/ailusion/itinerary/agents/budget.py)): tối đa `MAX_PRICE_FINDS` = 10 chỗ mỗi lộ trình, mỗi chỗ 1 `web_search` + 1 `fetch_page`; code mở lại link, đoạn trích và số tiền phải có trên trang.
3. **Ước** (`fill_price_estimates`, score.py), luôn ghi rõ "ước …":
   1. ≥ 2 chỗ cùng loại có giá thật trong chuyến → trung vị ("ước theo chỗ cùng loại");
   2. khớp bảng `PRICE_BY_TYPE` (**tự đặt**): công viên giải trí 700k · spa 400k · suối khoáng 300k · bảo tàng / di tích 50k · cà phê 60k · bãi biển, chùa, chợ… 0đ ("ước theo loại");
   3. chỗ chơi không khớp loại nào → trung vị giá thật mọi chỗ chơi của chuyến ("ước theo chỗ chơi khác"); không có thì `VISIT_PRICE` 100k;
   4. quán ăn không quán nào trong chuyến có giá → `MEAL_PRICE` 150k / người / bữa.

## 6. Planner

`day_target` ([agents/planner.py](../src/ailusion/itinerary/agents/planner.py)) = `day_allowance`. Luật 8 của Planner: các lịch trong ngày có mức tiền khác nhau quanh con số đó (1 thấp hơn, 1 cao hơn chút; lịch thứ 3 nếu có thì sát) để bước ghép chọn được tổ hợp sát ngân sách.

## 7. Chạy thử từng lịch: tiền một ngày ([coordinator/simulate.py](../src/ailusion/itinerary/coordinator/simulate.py))

| | Gồm |
|---|---|
| `plan_cost` (`metrics.cost`) | vé + ăn × số người |
| `day_spend` (`metrics.spend`) | vé + ăn + nghỉ trưa (ly cà phê / phòng theo giờ) + **đi lại** |
| Đi lại ([tabs.py](../src/ailusion/itinerary/tabs.py)) | Gọi xe: giá các hãng theo km thật ([shared/fares.py](../src/ailusion/itinerary/shared/fares.py)) · Tự lái: thuê xe + xăng theo ngày (`rental_of`) · Xe buýt, đi bộ (`bus_leg`): chặng ≤ 2 km đi bộ 0đ, xa hơn 1 vé `BUS_FARE` = 8.000đ mỗi người (tự đặt) |
| Vượt ngân sách ngày (`check_plan`) | `day_spend` > `day_allowance` → trạng thái "warn" + "vượt ngân sách ngày X đ"; nhóm P1 thì ghi lý do cần đổi chỗ rẻ hơn (lượt Planner sửa đang tắt — `PLANNER_FIXES`) |

Phát sinh trên tab Chi phí: `EXTRA_PCT` 10–15%, ngày mưa / lễ +5% vào mức cao, trần 25% (`extra_pct`, tabs.py).

## 8. Ghép 3 phương án ([coordinator/rank.py](../src/ailusion/itinerary/coordinator/rank.py))

- `trip_estimate` = (tổng `spend` các ngày + tiền phòng) × (1 + `EXTRA_MID`) + vé đi về mua trước — số hiển thị cho user.
- So với ngân sách thì bỏ vé đi về ra (`net_of_prepaid`): ngân sách đã trừ vé đó, không tính 2 lần. `band_gap` = lệch ra ngoài khoảng (âm = thấp hơn mức dưới, dương = vượt mức trên).
- `fit_budget`: thử mọi tổ hợp (`CANDIDATES_PER_DAY` = 5 lịch tốt nhất mỗi ngày), xếp theo:
  1. ít chỗ chơi lặp giữa các ngày
  2. đủ thứ user nhắc (VD bún bò)
  3. lệch ra ngoài khoảng ngân sách ít nhất
  4. ít quán lặp
  5. (PA2, PA3) ít trùng phương án trước
  6. các ngày tiêu đều quanh `day_allowance`
  7. hạng các lịch
- Sau khi ghép (`finish_trip`, `ghep`):
  - **Vượt mức trên** → `ask_user`: giữ lịch, hay đổi sang lịch rẻ nhất mỗi ngày theo tiền cả ngày (`day_money`: `spend`, thiếu thì `cost`).
  - **Thấp hơn / vượt** khoảng → `budget_note`: nhập 1 số thì so với số đó, chọn khoảng thì so với mức dưới / mức trên.
  - `money_notes`:
    - ngân sách < `base_daily_cost` × số người × số ngày → "thấp hơn mức chi tối thiểu thường gặp ở …";
    - "Khoảng X% tổng ước dựa trên giá ước" (phần tiền từ chỗ chưa có giá thật);
    - thấp hơn mức dưới → gợi ý tối đa `UPGRADE_STAYS` = 2 chỗ ở giá cao hơn vừa tiền còn dư (sao tin được cao trước) và `UPGRADE_VISITS` = 3 chỗ chơi có phí (giá thật) chưa có trong lịch.
  - `unlimited` → bỏ qua mọi luật tiền.

## 9. Hiển thị (FE)

- Thẻ phương án: "Ước tính cho N người: a–b triệu".
- Tab **Chi phí** ([plan-cost.tsx](../../ailusion-journey-fe/src/components/itinerary/plan-cost.tsx)): chỗ ở / vé / ăn / đi lại / phát sinh, khoảng thấp – cao, ghi "ước" với giá ước.
- Ghi chú ngân sách (lệch, giá ước, gợi ý nâng) hiện trong phần ghi chú của lộ trình.

---

## Số tự đặt còn lại

| Số | Giá trị | Ở đâu |
|---|---|---|
| `BUDGET_BAND` | ±5% | shared/form.py (user yêu cầu) |
| `EXTRA_PCT` | 10–15% phát sinh (+5% mưa / lễ, trần 25%) | shared/form.py, tabs.py |
| `STAY_TARGET_SHARE` / `STAY_BUDGET_SHARE` | 35% / 50% | score.py |
| `STAY_BAND` | 60–140% | search.py |
| 2 người 1 phòng | | rank.py `stay_cost` |
| `PRICE_BY_TYPE`, `VISIT_PRICE` 100k, `MEAL_PRICE` 150k | | score.py |
| `BUS_FARE` | 8.000đ / người / lượt | tabs.py |

## Còn để ý

- Chat nhận "không được vượt" (`budget_hard = true`) nhưng khi đổ sang form chưa tự chọn "Sang, không vượt ngân sách" — form vẫn là "Bình thường" (±5%).
- Gợi ý nâng chỗ ở chỉ là gợi ý, không tự đổi: đổi chỗ ở thì phút đi tới mọi điểm đổi theo, lịch phải xếp lại.
- Chọn "Trên 10 triệu" thì form FE không có số ở ô ngân sách (chỉ có mức dưới); bấm lập lộ trình vẫn được.
