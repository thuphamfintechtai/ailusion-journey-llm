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
        └─ luôn: % tổng ước là giá ước

Đầu kết quả: ngân sách so với mặt bằng giá của điểm đến, mỗi mức chi một trung vị (mục 10)
```

---

## 1. Nhập ngân sách

| Ở đâu | Làm gì |
|---|---|
| Form FE ([trip-form-sheet.tsx](../../ailusion-journey-fe/src/components/itinerary/trip-form-sheet.tsx)) | Chip 3 / 5 / 8 / 12 / 20 / 30 triệu + ô gõ tuỳ ý (`BUDGETS`) |
| `budget_unit` | `group_trip` (cả đoàn cả chuyến, mặc định) · `person_trip` (mỗi người cả chuyến) · `person_day` (mỗi người mỗi ngày) |
| `spend_style` | `normal` (±5%) · `luxury_within_budget` — FE ghi "Sang, không vượt ngân sách" · `unlimited` (bỏ mọi luật tiền, chọn theo chất lượng) — FE ghi "Không ngại chi thêm" (trước ghi "Không giới hạn", trùng chữ với ô ngân sách mà khác nghĩa) |
| `budget_unknown` | Ô ngân sách chọn **"Không giới hạn" = chưa biết bao nhiêu**, KHÔNG phải chi thoải mái: không hỏi lại H3; chỗ ở nhắm giá phòng thường gặp ở điểm đến (trung vị `price_stats_vnd`, `stay_band_calls` + chọn chỗ ở ở `cham`) — không có ngân sách thì code vốn lấy chỗ sao cao nhất (resort); không đặt tiền mỗi ngày (không báo "vượt ngân sách ngày"); đầu kết quả khung "Chưa có ngân sách" (`kind` `unknown`): "Lịch theo mức chi thường gặp ở Nha Trang (phòng khoảng 1,4 triệu/đêm), không chọn chỗ đắt nhất: tổng ước … Có ngân sách thì nhập để lịch bám theo" |
| `budget_min` | Mức dưới khi user chọn **khoảng** ở câu hỏi H3. FE tự xoá khi user nhập lại ngân sách / đổi cách tính (`set` trong trip-form-sheet) |
| `prepaid_tickets` | Tiền vé đi về đã mua, mỗi người — trừ khỏi ngân sách |
| Chat ([chat/trip_info.py](../src/ailusion/chat/trip_info.py), [chat/prompts.py](../src/ailusion/chat/prompts.py)) | Tự đọc `budget_million` ("15tr", "2 triệu mỗi người") và `budget_hard` (có được vượt không). FE `groupBudget` đổi form về cả đoàn cả chuyến khi báo cho chat; `budget_hard = true` khi mức chi "Sang, không vượt" |
| Câu hỏi H3 (`questions_for`, [coordinator/profile.py](../src/ailusion/itinerary/coordinator/profile.py)) | Thiếu ngân sách (và không chọn "Không giới hạn") thì hỏi, mỗi lựa chọn là 1 khoảng: Dưới 3tr = tối đa 3tr · 3–6tr · 6–10tr · Trên 10tr = từ 10tr, không trần · Chưa biết = `budget_unknown` |

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
- `price_levels`: mặt bằng giá của CẢ điểm đến — `price_stats_vnd` (p25 / trung vị / p75) của `find_stays` (giá phòng / đêm, mọi chỗ ở có giá) và `find_places` loại "Nhà hàng" (giá mỗi người, mọi quán có giá), MCP tính trước khi cắt `limit`. Ngân sách thấp so với mặt bằng thì bước 3 ghi ngay vào nhật ký (trước khi lập lịch) và kết quả báo ở đầu (mục 10).
  - Thay `base_daily_cost` cũ (trung vị 20 chỗ ở "phổ biến" ÷ 2 + 3 bữa "Nhà hàng" phổ biến, × số NGÀY): Phú Quốc 2 người 4 ngày ra "tối thiểu 25 triệu" vì 20 chỗ phổ biến toàn resort (trung vị 3,96 triệu / đêm), trong khi trung vị cả 150 chỗ ở có giá là 1,40 triệu.

## 4. Chỗ ở — phần lớn nhất của ngân sách ([coordinator/score.py](../src/ailusion/itinerary/coordinator/score.py), [coordinator/search.py](../src/ailusion/itinerary/coordinator/search.py))

| Số | Giá trị | Nguồn |
|---|---|---|
| `STAY_TARGET_SHARE` | tiền phòng nhắm **35%** mốc | tự đặt |
| `STAY_BUDGET_SHARE` | trần **50%** mốc; "sang, không vượt" nhắm luôn mức này (`stay_target_share`) | tự đặt |
| `STAY_BAND` | tìm thêm phòng giá **60%–140%** mục tiêu / đêm / phòng (`stay_band_calls`) | tự đặt |
| `STAY_PRICE_PICKS` / `STAY_PRICE_SLACK` | lấy 8 chỗ giá sát mục tiêu, cho lệch thêm 25% để được chỗ gần các điểm chơi hơn | tự đặt |
| Số phòng | 2 người 1 phòng, làm tròn lên; trẻ dưới 6 tuổi ngủ chung giường bố mẹ, không tính (`rooms_for`, shared/form.py — dùng chung cho mọi bước) | tự đặt |

Cách chọn:
1. `usable_stays`: bỏ chỗ cách trung tâm > 30 km, bỏ chỗ tiền phòng cả chuyến > 50% mốc (`unlimited` thì không bỏ theo giá).
2. Mục tiêu tiền phòng = mốc × `stay_target_share` → 8 chỗ giá sát nhất → chỗ ít phút đi tới 12 điểm chơi nổi bật nhất.
3. User đã đặt chỗ ở (`stay_booked`) → tiền phòng 0.

## 5. Giá vé, giá ăn

1. **DB**: giá Google ghi (VD "100–200 N ₫" → số giữa).
2. **Agent Budget** ([agents/budget.py](../src/ailusion/itinerary/agents/budget.py)) — chỉ chỗ chơi sắp đưa Planner đang dùng giá ước, tối đa `MAX_PRICE_FINDS` = 10 chỗ. **Agent không gọi tool**, code làm hết:
   1. `web_search` mỗi chỗ 1 lượt ("giá vé <tên> <điểm đến>", 3 kết quả).
   2. Agent (1 lượt LLM cho mọi chỗ) đọc đoạn tóm tắt Tavily (`SNIPPET_CHARS` 600) + tối đa `PRICE_WINDOWS` 2 đoạn quanh số tiền / "miễn phí" trong trang (mỗi bên `WINDOW_CHARS` 150) — không phải 700 ký tự đầu trang (toàn menu, logo, ảnh). Trả `{code, price (VND / người lớn, 0 = miễn phí, null = không có), url, quote}`; `price: ""` cũng hiểu là null.
   3. Code lọc (`trusted_finds`): link phải là kết quả tìm của chính chỗ đó; một câu trích dùng cho 2 chỗ thì bỏ cả hai.
   4. Code mở lại link (`fetch_page`, mỗi link 1 lần) và nhận khi: đoạn trích có trên trang (bỏ markdown rồi so; trích nối "[...]" thì từng đoạn phải có), số tiền có trong đoạn trích (đọc được "80.000 VNĐ", "120k", "1,2 triệu"), giá 0 thì đoạn trích ghi "miễn phí / không mất phí / không thu phí / free", và trang nhắc lõi tên chỗ liền một cụm (`name_on_page`).
   5. Nhận → `price_by = "Agent Budget: <link>"`; sau đó mọi giá ước tính lại một lượt (`refresh_estimates`, coordinator/filter.py) vì trung vị cùng loại đã có thêm giá thật.

   Lộ trình Hội An 10/2026: trước sửa 0/10 giá được nhận; sau sửa 5–6/10, đúng cả (Chùa Cầu / Phố Cổ 80.000đ vé phố cổ, Ký Ức Hội An 540.000đ, Làng Gốm Thanh Hà 35.000đ, Chùa Pháp Bảo và Precious Heritage miễn phí).
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
    - "Khoảng X% tổng ước dựa trên giá ước" (phần tiền từ chỗ chưa có giá thật);
    - thấp hơn mức dưới → gợi ý tối đa `UPGRADE_STAYS` = 2 chỗ ở giá cao hơn vừa tiền còn dư, trong `STAY_MAX_KM` từ trung tâm (sao tin được cao trước; chỗ bước chọn chỗ ở đã bỏ vì xa thì không gợi ý) và `UPGRADE_VISITS` = 3 chỗ chơi có phí (giá thật) chưa có trong lịch.
  - `unlimited` → bỏ qua mọi luật tiền.

## 9. Hiển thị (FE)

- Đầu kết quả: khung mức chi (`budget_level`, mục 10) — đỏ khi ngân sách dưới cả mức Tiết kiệm, xanh khi dưới mức của mức chi đã chọn / ngân sách dư / mức chi không giới hạn (thiếu hoặc dư thì có nút sửa form); bảng 3 mức (cả chuyến, phòng / đêm, quán / người), cột đem so với ngân sách tô nền; dòng "Ngân sách của bạn" ghi phép quy đổi.
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
| 2 người 1 phòng, trẻ dưới 6 ngủ chung | | shared/form.py `rooms_for` |
| `PRICE_BY_TYPE`, `VISIT_PRICE` 100k, `MEAL_PRICE` 150k | | score.py |
| `BUS_FARE` | 8.000đ / người / lượt | tabs.py |

## Còn để ý

- Chat nhận "không được vượt" (`budget_hard = true`) nhưng khi đổ sang form chưa tự chọn "Sang, không vượt ngân sách" — form vẫn là "Bình thường" (±5%).
- Gợi ý nâng chỗ ở chỉ là gợi ý, không tự đổi: đổi chỗ ở thì phút đi tới mọi điểm đổi theo, lịch phải xếp lại.
- Chọn "Trên 10 triệu" thì form FE không có số ở ô ngân sách (chỉ có mức dưới); bấm lập lộ trình vẫn được.

## 10. Mức chi của điểm đến ([coordinator/levels.py](../src/ailusion/itinerary/coordinator/levels.py))

Mỗi mức = (phòng × số **đêm** × số phòng (`rooms_for`: 2 người 1 phòng, trẻ dưới 6 ngủ chung) + 1 bữa × số bữa bắt buộc (theo giờ rảnh từng ngày) × số người) × (1 + `EXTRA_MID`), cộng phát sinh như tổng ước của lịch. Chưa gồm vé, đi lại: ngân sách dưới mức này là chắc thiếu, không báo nhầm. Đã đặt chỗ ở thì chỉ còn tiền ăn. Số hiện ra làm tròn **lên** 100 nghìn (làm tròn thường từng ra "khoảng 8.000.000 đ, hơn ngân sách 8.000.000 đ"); so ngân sách bằng số chưa làm tròn.

| Mức của điểm đến | Giá phòng / đêm, giá quán / người | Phú Quốc 2 người 4 ngày 3 đêm (DB 10/2026) |
|---|---|---|
| Tiết kiệm | trung vị nửa rẻ (p25) | 0,80 triệu + 163 nghìn → ~6,8 triệu |
| Thường gặp | trung vị | 1,40 triệu + 250 nghìn → ~11,0 triệu |
| Sang | trung vị nửa đắt (p75) | 3,25 triệu + 350 nghìn → ~19,7 triệu |

Mỗi mức chi user chọn so ngân sách (`trip_budget`) với một mức riêng và có lời báo riêng:

| Mức chi | Đem so với | Dưới cả Tiết kiệm (đỏ, `low`) | Dưới mức đem so (xanh, `below`) | Đủ |
|---|---|---|---|---|
| Bình thường | Thường gặp | "Ngân sách thấp so với …": mức tiết kiệm đã khoảng X, hơn ngân sách; lịch ưu tiên chỗ rẻ nhưng tổng vẫn có thể vượt; mức thường gặp khoảng Y | "Ngân sách dưới mức thường gặp ở …": đủ mức tiết kiệm nên lịch ưu tiên chỗ ở, quán rẻ hơn mức giữa | không báo; dư thì xem dưới |
| Sang, không vượt ngân sách | Sang | như trên, kèm "chưa nói tới mức sang (khoảng Z)" | "Ngân sách chưa tới mức sang ở …": lịch chọn chỗ tốt nhất mà không vượt ngân sách | không báo; dư thì xem dưới |
| Không giới hạn | không so ngân sách | — | luôn báo (xanh): lịch chọn theo chất lượng, tổng ước của phương án đề xuất; cao hơn ngân sách thì nói | — |

- **Ngân sách dư** (xanh, `surplus`, chỉ biết sau khi lập lịch): ngân sách trên cả mức Sang mà tổng ước của phương án đề xuất vẫn dưới khoảng ngân sách → "Ngân sách dư so với …: lịch ước khoảng E, thấp hơn ngân sách khoảng P%: cả mức sang … cũng chỉ khoảng L, nên khó tiêu hết ngân sách", kèm nút sửa form; ghi chú cùng ý ở cuối kết quả (`budget_note`) thì bỏ. Ngân sách chưa tới mức Sang mà lịch tiêu ít thì không báo dư (lý do là lịch, không phải điểm đến rẻ).
- **Phép quy đổi ngân sách** (`budget_text`, theo đúng `_to_trip`): khung báo ghi "1 triệu mỗi người mỗi ngày × 2 người × 3 ngày = 6 triệu cả đoàn cả chuyến" (vé đã mua: "10 triệu − vé đã mua 1,5 triệu × 2 người = 7 triệu"; khoảng: "3–6 triệu cả đoàn cả chuyến, so với giữa khoảng 4,5 triệu"). Đơn vị "mỗi người" / có vé đã mua thì bước phân nhóm ghi ngay vào nhật ký; bong bóng yêu cầu ghi "(= … cả đoàn cả chuyến)" và đúng khoảng ngân sách user chọn. Bấm nhầm "mỗi người mỗi ngày" (6 triệu thành 36 triệu) là thấy ngay.
- Chỉ để báo user, không dùng để lọc / chọn chỗ (chọn chỗ vẫn theo mục 4–7).
- Ngân sách dưới mức đem so mà có quán / vé đắt hơn phần tiền mỗi ngày (`costly_ids`) thì bước chốt danh sách ghi thêm "đã ưu tiên quán dưới … mỗi người mỗi bữa và chỗ chơi vé dưới … mỗi người".
- MCP chưa trả `price_stats_vnd` cho quán (bản cũ) thì không báo: không đoán từ vài quán phổ biến nhất.
- Giá quán trên Google Maps là khoảng giá ("100–200 N ₫" → 150 nghìn); quán bình dân hay không ghi giá nên mức Tiết kiệm có thể cao hơn thực tế đi bụi.

### Kịch bản đã thử ([tests/test_budget_levels.py](../tests/test_budget_levels.py), giá thật trong DB 10/2026)

| Kịch bản | Kết quả |
|---|---|
| Chỗ đắt, ngân sách thấp: Phú Quốc 2 người 3N2Đ 4 triệu | đỏ: tiết kiệm ~4,4 triệu (phòng + ăn tiết kiệm 3,9 triệu + phát sinh) |
| Chỗ đắt, ngân sách vừa / cao: Phú Quốc 8 / 20 triệu | không báo |
| Chỗ rẻ, ngân sách thấp: Huế 2 người 3N2Đ 2 triệu | đỏ |
| Chỗ rẻ, ngân sách rất cao: Huế 30 triệu (Bình thường / Sang) | xanh "Ngân sách dư so với Huế" (chạy thật: lịch ước 11,9 triệu, thấp hơn ~60%; mức sang ~6,8 triệu; chỗ ở Azerai 3,4 triệu / đêm, đắt nhất trong 30 km) + gợi ý nâng chỗ ở, hoạt động có phí (mục 8) |
| Bấm nhầm đơn vị: 6 triệu "mỗi người mỗi ngày" (Đà Nẵng 2 người 3 ngày) | nhật ký + bong bóng ghi 36 triệu cả đoàn; xanh "Ngân sách dư" kèm phép quy đổi |
| Đơn vị: 6 triệu cả đoàn = 3 triệu mỗi người cả chuyến = 1 triệu mỗi người mỗi ngày (2 người 3 ngày) | cùng mốc 6 triệu, cùng lời báo |
| Đông người, ngân sách không cao: Đà Nẵng 8 người 3N2Đ 10 triệu (4 phòng) | đỏ |
| Gia đình 2 lớn + 2 trẻ: Nha Trang 8 triệu | đỏ (2 phòng, 4 suất ăn) |
| Khoảng "Dưới 3 triệu" / "3–6 triệu" / "Trên 10 triệu" (Hội An 2 người) | mốc 3 / 4,5 (giữa khoảng) / 10 triệu (mức sàn) |
| Mức chi Sang: Phú Quốc 12 triệu | xanh: chưa tới mức sang (~12,9 triệu) |
| Vé đã mua 1,5 triệu / người, ngân sách 10 triệu cả đoàn | mốc 7 triệu |
| Đã đặt chỗ ở / đi trong ngày | chỉ còn tiền ăn |

