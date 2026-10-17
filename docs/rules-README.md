# Toàn bộ luật lập lộ trình — để review

Liệt kê mọi luật của pipeline lập lộ trình (LLM service) theo thứ tự các bước, kèm giá trị, chỗ
khai báo và nguồn: **dữ liệu** (lấy từ DB / tool), **user** (user yêu cầu), **tự đặt** (số tạm).
Đường dẫn tính từ `ailusion-journey-llm/src/ailusion/itinerary/`. Chi tiết riêng:
[budget-README.md](budget-README.md) (ngân sách), [planner-README.md](planner-README.md) (Planner),
[backlog-README.md](backlog-README.md) (việc còn mở).

Mục lục: [0. Pipeline](#0-pipeline) · [1. Form](#1-form-và-đọc-lời-nhắn) · [2. Nhóm khách](#2-nhóm-khách-và-nhu-cầu) ·
[3. Giờ rảnh](#3-giờ-rảnh-từng-ngày) · [4. Bữa ăn](#4-bữa-ăn) · [5. Hỏi lại](#5-hỏi-lại) ·
[6. Tìm chỗ](#6-tìm-chỗ) · [7. Phân loại](#7-phân-loại-chỗ) · [8. Lọc](#8-lọc) · [9. Chấm](#9-chấm-và-chỗ-ở) ·
[10. Chia nhánh, chốt danh sách](#10-chia-nhánh-chốt-danh-sách-chia-ngày) · [11. Planner](#11-planner) ·
[12. Chạy thử, tự sửa](#12-chạy-thử-và-tự-sửa-lịch) · [13. Xếp hạng, ghép](#13-xếp-hạng-và-ghép-phương-án) ·
[14. Decision](#14-agent-decision) · [15. Hiển thị](#15-lý-do-hiển-thị) · [16. MCP](#16-luật-phía-mcp) ·
[Cần review](#cần-review)

---

## 0. Pipeline

`nhan_form → doc_loi_nhan → phan_nhom → (hoi_lai | tim_kiem) → loc → cham → chot_nhanh → xac_minh → lap_phuong_an → ghep → quyet_dinh → tra_ket_qua`

| Luật | Giá trị | Ở đâu |
|---|---|---|
| Số phương án / nhánh song song | `BRANCHES` = 3 | coordinator/branches.py |
| Nhánh lỗi | chạy lại 1 lần, vẫn lỗi thì bỏ nhánh đó (còn nhánh khác) | `all_branches` |
| Model | `openai:gpt-4.1-mini`, không thinking | — |

## 1. Form và đọc lời nhắn

**Form** (`TripForm`, shared/form.py): điểm đến, ngày đi / về, số người (1–30), ngân sách + `budget_min` + `budget_unit`, chỗ ở đã đặt, vé đi về đã mua, phương tiện (`motorbike` · `car` · `bus` · `taxi`), người đi cùng, nhu cầu bắt buộc, giờ tới / đi / dậy / về tối, lịch bận, đi khuya, nghỉ trưa, mục đích (tối đa 2), sở thích, mức đi bộ M1–M4, chỗ muốn đi (tối đa 10), ưu tiên, mức chi.

**Kiểm form** (`TripForm._check`) — sai thì từ chối ngay, không chạy pipeline:

| Luật | Báo |
|---|---|
| Ngày về trước ngày đi | "Ngày về phải sau hoặc bằng ngày đi" |
| Quá 7 ngày | "Tối đa 7 ngày" |
| Chỗ muốn đi hẹn ngày ngoài chuyến | "{chỗ}: chuyến chỉ có N ngày, không có ngày M" |
| Đi trong ngày, giờ khởi hành về ≤ giờ đến (đến 15:00, về 09:00) | "Đi trong ngày: giờ khởi hành về (09:00) phải sau giờ đến (15:00)" — trước đây lập ra cả chuyến trống, không nói gì |
| Giờ dậy ≥ giờ về tối (dậy 21:00, về 20:00); chỉ so khi giờ về từ 12:00 và không đi khuya | "Giờ thức dậy (21:00) phải trước giờ về chỗ ở buổi tối (20:00)" |
| "Một mình" mà số người > 1; nhóm khác mà 1 người | "Đi một mình nhưng số người là 3: sửa 'Số người' hoặc 'Đi cùng ai'" — FE chọn "Một mình" thì số người thành 1, câu hỏi H1 "Một mình" cũng vậy (form mặc định 2 người) |
| Số trẻ em + người từ 65 > số người | "Số trẻ em và người từ 65 tuổi (3) nhiều hơn số người (2)" |
| Có trẻ em mà không có người lớn (số trẻ ≥ số người; người từ 65 tính là người lớn) | "Có 1 trẻ em thì số người (1) phải gồm cả người lớn đi cùng" — FE dựng form từ chat: chưa rõ số người lớn thì tính người đang chat là 1 |
| Số người ngồi xe lăn > số người | "Số người ngồi xe lăn (3) nhiều hơn số người (2)" |

Lỗi trả 422, `detail` là câu tiếng Việt (`form_error_text`, api/app.py: bỏ tiền tố "Value error, " của pydantic; lỗi kiểu dữ liệu ghi kèm tên ô); FE hiện ở thẻ lỗi kèm nút "Mở lại form".

**Agent Intake** (agents/intake.py) — đọc lời nhắn, điền ô còn trống:

| # | Luật |
|---|---|
| I1 | Chỉ điền ô trong danh sách "Ô còn trống"; ô user đã điền không đụng |
| I2 | Mỗi ý kèm `quote` chép NGUYÊN VĂN lời user; code kiểm câu trích có thật, không có thì bỏ ý đó |
| I3 | Ô có danh sách giá trị: chỉ dùng đúng 1 giá trị; không chắc thì bỏ |
| I4 | Không tự đặt số, không đổi đơn vị (giữ "1,5 triệu", "5 giờ chiều"; code tự đổi) |
| I5 | Không chọn nhóm khách, không đoán điều user không nói |
| I6 | Món ghi ở ô "Nhu cầu khác" (VD "bún bò") vào `interests` (`foods_in_note`); cụm có "không / ko / kiêng / tránh / dị ứng / hạn chế" ở bất kỳ đâu ("bé dị ứng hải sản") hay bắt đầu bằng "ít" là nhu cầu, không thành món — trước đây chỉ xét đầu cụm, "bé dị ứng hải sản" thành món lịch phải có; "tiết kiệm" không phải ưu tiên |
| I7 | Mã "có món chay / thuần chay" chỉ nhận khi câu trích có chữ chay, vegetarian, vegan, không ăn thịt (`diet_said`) — chạy thật, Intake từng đổi "bé dị ứng hải sản" ra "có món chay" (mọi quán bị ghi "chưa rõ đáp ứng: có món chay") |

## 2. Nhóm khách và nhu cầu

**Bảng A** (`TABLE_A`) — người đi cùng → nhu cầu:

| Mã | Ai | Bắt buộc | Mong có |
|---|---|---|---|
| A2 | trẻ dưới 6 | — | kids, high_chair, baby_changing |
| A3 | trẻ 6–12 | — | kids, playground |
| A4 | từ 65 tuổi | — | restroom |
| A5 | xe lăn | wheelchair, wheelchair_toilet | — |
| A6 | cặp đôi | — | romantic |
| A7 | bạn bè, đồng nghiệp | — | groups |
| A8 | một mình | — | solo_dining |

`needs_care` = có trẻ dưới 6 / người từ 65 / xe lăn. Cột "tối về trước" của bảng A **không còn áp** — giờ về tối chỉ theo user.

Chưa chọn "Đi cùng ai" (`group_of`): 1 người là một mình, có trẻ em / người từ 65 là gia đình — không hỏi lại H1 (trước đây hỏi, bấm "Gia đình có trẻ nhỏ" còn ghi đè 2 bé user nhập thành 1). Số phòng (`rooms_for`): 2 người một phòng, trẻ dưới 6 tuổi ngủ chung giường bố mẹ (khách sạn thường không tính thêm) — trước đây gia đình 2 người lớn + 2 bé dưới 6 tính 2 phòng; vé, bữa ăn của trẻ vẫn tính như người lớn (chưa có giá trẻ em).

**Trẻ em và món phải tránh** (shared/companions.py; danh sách chữ và lời dặn là tự đặt):

| Luật | Giá trị |
|---|---|
| Đoàn có trẻ dưới 6 tuổi | bỏ chỗ nhóm spa / massage / quán bar (`KID_UNFIT_GROUPS`) và trò mạo hiểm có chữ lặn biển, lặn bình, scuba, dù lượn, nhảy dù, zipline, đu dây (`KID_UNFIT_WORDS`), trừ chỗ user chọn; ghi riêng "Đoàn có 2 bé dưới 6 tuổi: bỏ 40 chỗ spa, massage, quán bar, lặn biển, dù lượn (không hợp trẻ nhỏ) — …" — chạy thật Nha Trang: phương án nào cũng có spa (mục đích "nghỉ ngơi" khớp spa), rồi "Lặn Biển LabiXa" |
| Món phải tránh (ô "Nhu cầu khác": dị ứng / kiêng / tránh / không ăn …, `avoided_foods`) | bỏ quán có món đó trong tên / loại (`AVOID_WORDS`: "hải sản" gồm cua, ghẹ, tôm, mực, ốc, sò, hàu, cá…; khớp nguyên chữ có dấu, "cá" không khớp "cà phê"), ghi "Bé dị ứng hải sản: bỏ 3 quán có món đó — …; quán trong lịch vẫn nên dặn trước khi gọi món"; món không nhận ra (cay, đậu phộng) ghi "chưa lọc được quán theo món này — dặn quán trước khi gọi món" — chạy thật: "Bánh Canh Cua Linh" từng được xếp 2 lần |
| Lời dặn vì có trẻ (`kid_tips` → `Day.kid_notes`) | đoàn có trẻ em: chỗ chơi trong ngày khớp thú (khỉ, vườn thú) · tắm bùn, suối nước nóng · bãi biển, hồ bơi, công viên nước · tàu ra đảo / vịnh / tour · thác, suối · núi, hang, bậc thang, cáp treo, hải đăng · chợ → "Bãi biển Nha Trang: trông trẻ sát khi xuống nước, mang phao tay cho trẻ, che nắng" (mỗi chỗ 1 lời dặn, dòng khớp đầu tiên); web hiện dưới lịch ngày, bản chữ chat in "👶" |

**Nhu cầu bắt buộc theo loại chỗ** (`MEAL_NEEDS`, `needs_for`, shared/form.py): nhu cầu ăn uống — có món chay, thuần chay, ghế cao cho bé, thực đơn trẻ em, đồ ăn khuya, bữa sáng, đồ uống có cồn, phòng ăn riêng — chỉ xét ở **quán**; nhu cầu khác (lối / nhà vệ sinh xe lăn, chỗ đỗ ô tô, hợp trẻ em…) xét mọi chỗ. Trước đây bãi biển, bảo tàng, khách sạn cũng bị ghi "chưa rõ có đáp ứng nhu cầu bắt buộc" khi chọn "Có món chay".

| Bước | Luật |
|---|---|
| Tìm | một lượt `find_places` theo mọi nhu cầu (ra quán đáp ứng hết), một lượt theo nhu cầu không phải ăn uống (chỗ chơi); `find_stays` chỉ gửi nhu cầu không phải ăn uống |
| Lọc | chỗ Google ghi rõ "không có" → bỏ, ghi "Chùa Linh Ứng: thiếu nhu cầu bắt buộc: lối vào cho xe lăn"; chưa rõ → giữ, ghi "Hải sản Bé Mặn: chưa rõ đáp ứng: có món chay, ghế cao cho bé — nên hỏi trước" |
| Danh sách đưa Planner | quán đáp ứng mọi nhu cầu ăn uống xếp trước quán chưa rõ (cùng mức giá); quán dự phòng để code bù bữa cũng vậy |
| Planner | dòng "# Đoàn cần (bắt buộc): có món chay, ghế cao cho bé (quán Q…); lối vào cho xe lăn (mọi chỗ)"; mỗi chỗ ghi "đáp ứng: …", "chưa rõ: …", "không có: …"; luật 11: chọn chỗ "đáp ứng" trước, "chưa rõ" chỉ khi thiếu, không dùng "không có" trừ chỗ bắt buộc |
| Chạy thử | chỗ "không có" nhu cầu áp cho loại của nó → lỗi "… thiếu nhu cầu bắt buộc: …" |

**Nhóm khách** (`profile_trip`, coordinator/profile.py) — dòng đúng đầu tiên:

| Luật | Điều kiện | Nhóm |
|---|---|---|
| B0 | user chọn ưu tiên | saving→P1 tiết kiệm · comfort→P2 đỡ mệt · leisure→P3 thong thả · short_time→P4 ít thời gian · many_places→P5 đi nhiều chỗ |
| B1 | quá nửa số ngày rảnh < `LOW_FREE_MINUTES` (300 phút), hoặc chuyến ≤ 2 ngày mà mục đích chính không phải nghỉ ngơi / phiêu lưu | P4 |
| B2 | `needs_care` | P2 |
| B3 | *(đã bỏ — ngân sách thấp → tiết kiệm làm cả chuyến tiêu thấp hơn ngân sách 35%)* | — |
| B4 | còn lại | P3 |

Mục đích phiêu lưu + `needs_care` → mức đi bộ tối đa M1.

## 3. Giờ rảnh từng ngày

| Luật | Giá trị | Nguồn |
|---|---|---|
| Ngày bắt đầu khi không nhập giờ (`DEFAULT_ARRIVAL`, `DEFAULT_WAKE`) | 08:00 | tự đặt (user yêu cầu 8h) |
| Ngày 1 có giờ tới cụ thể | giờ tới + ra khỏi bến `HUB_EXIT_MIN` 15 + bến→chỗ ở `HUB_TRAVEL_MIN` 30 | tự đặt |
| Giờ về tối không nhập | `DEFAULT_NIGHT_RETURN` 20:45 = chơi tới `DEFAULT_EVENING_UNTIL` 20:00 + đường về `EVENING_RIDE_BACK` 45 | user ("chưa biết thì 20h") |
| Đi khuya (`night_out`) | ngày kết thúc 23:30 | — |
| User hẹn chỗ buổi tối muộn hơn giờ về mặc định | kéo ngày tới giờ hẹn + `MUST_EVENING_STAY` 90 phút (tối đa 23:30) | tự đặt |
| Ngày cuối không nhập giờ đi | `DEFAULT_DEPARTURE` 15:00; kết thúc = giờ đi − thủ tục `CHECKIN_MIN` 90 − dự phòng `DEPARTURE_BUFFER_MIN` 30 − bến 30 | tự đặt |
| Lịch bận | chặn từ (giờ bận − phút đi tới chỗ bận, mặc định `BUSY_TRAVEL_MIN` 15) tới giờ xong; thiếu giờ → hỏi lại H5; bận hẳn ngoài giờ của ngày (bận 07:00–09:00 mà tới nơi buổi chiều) → bỏ, không chặn gì. Bước chạy thử tự chèn khung bận vào lịch như nghỉ trưa: chỗ nào không kịp xong trước giờ bận thì đi sau giờ bận (+`BUSY_TRAVEL_MIN` từ chỗ bận); bận sau chỗ cuối thì đi bận rồi về; giờ bận tới trong lúc nghỉ trưa thì không nghỉ; lịch ngày hiện mục "Lịch bận" đúng giờ user nhập. Ngày cuối bận xong sau giờ phải rời chỗ ở (bận 11:00–13:00, rời 12:30 để kịp bay 15:00) → ghi "Lịch bận ngày 3 (11:00–13:00) kết thúc sau giờ phải rời chỗ ở (12:30) để kịp chuyến về: lịch ngày này chỉ xếp tới trước giờ bận — kiểm lại giờ bận hoặc giờ về" (`time_notes`, bước phân nhóm). Bận kéo tới / qua giờ kết thúc ngày như ca đó thì không dời chỗ nào ra sau giờ bận (sau đó không còn giờ): chỗ không kịp xong trước giờ bận là lỗi "chạm giờ bận" — bước bù chỗ không thêm, bước cắt bớt bỏ; Planner được ghi "bận tới hết ngày: mọi chỗ phải xong trước 10:45". Trước đây chỗ bị dời ra sau giờ bận, về 16:54 (chỉ 1 lỗi "về trễ" dù dời bao nhiêu chỗ, nên bước bù chỗ còn nhét thêm). Ngày cuối bận xong sau giờ rời chỗ ở thì lịch kết thúc bằng mục `depart` (icon máy bay) "13:00 Từ chỗ bận ra thẳng sân bay / ga / bến xe, kịp chuyến về 15:00 (trả phòng trước giờ bận)" — trước đây "Về <chỗ ở> 13:15, kết thúc chuyến"; đường trên bản đồ dừng ở chỗ cuối, không vẽ về chỗ ở (`depart_after`, dùng chung cho dòng lịch và `fill_paths`) | — |
| Nghỉ trưa | ngày bắt đầu ≤ `NAP_IF_START_BY` 09:00 **và** kết thúc ≥ `NAP_IF_END_FROM` 17:00, `nap` ≠ none → nghỉ `NAP_MIN` 90 phút; giờ rảnh trừ 90 phút | user |
| Chỗ nghỉ trưa (`auto`) | quán trưa → chỗ ở ≤ `NAP_HOME_MAX` 20 phút thì về chỗ ở, xa hơn thì quán cà phê gần quán trưa (≤ `NAP_SPOT_KM` 4 km). User chọn khách sạn / quán cà phê mà không có chỗ nào trong 4 km quanh quán trưa → nghỉ ở chỗ ở, ghi "không có khách sạn trong 4 km quanh quán trưa: nghỉ trưa ở chỗ ở" (trước đây đổi chỗ không báo) | tự đặt |
| Giá nghỉ trưa | quán cà phê `CAFE_REST_PRICE` 50k/người · khách sạn `HOTEL_REST_SHARE` 0,5 × giá phòng 1 đêm | tự đặt |
| Chỗ user hẹn buổi | sáng 08:00–11:30 · chiều 13:30–17:30 · tối 18:00–21:30 (`MUST_BUCKETS`); trễ giờ hẹn > `MUST_LATE_MAX` 20 phút là lỗi | tự đặt |

## 4. Bữa ăn

| Luật | Giá trị | Nguồn |
|---|---|---|
| Khung giờ ăn (`MEALS`) | sáng 06:30–10:00 · trưa 10:30–14:00 · tối 18:00–20:00 | user |
| Mốc chờ tới (`*_TARGET`) | sáng 06:30–08:30 · trưa 11:30–13:00 (dịch tối đa 1 tiếng) · tối 18:00 | user |
| Bữa bắt buộc theo giờ bắt đầu ngày (`required_meals`) | ≤ 08:00: đủ sáng + trưa + tối · 08:00–11:00: bữa sáng tuỳ chọn · sau 11:00: không bữa sáng · từ 13:00: không ép bữa trưa · ngày phải phủ ≥ `MEAL_MIN_OVERLAP` 30 phút của khung mốc VÀ cả khung giờ ăn còn chứa được bữa ngắn nhất (`MEAL_STAY` 45 phút): về chỗ ở trước 18:30 / bay về 21:00 (rời chỗ ở 18:30) thì không bắt bữa tối, ghi "Không xếp bữa tối: ngày kết thúc 18:30"; tới nơi 19:45 thì "Không xếp bữa tối: ngày bắt đầu 19:45". Bữa cuối ngày không kịp ăn rồi về (về trước 18:45) thì bỏ, không báo thiếu bữa, ghi "không đủ giờ ăn tối … ăn gần chỗ ở" | user |
| Bữa trong giờ bận (`busy_meals`, `needed_meals`) | giờ bận / dự tiệc phủ ≥ `MEAL_MIN_OVERLAP` 30 phút của khung giờ ăn (bận 11:00–13:00 phủ bữa trưa) → không bắt xếp quán bữa đó, không báo "đi suốt giờ ăn trưa mà không có quán ăn" (ăn ở / quanh chỗ bận; trước đây bước đặt quán tự chèn quán trưa 13:15 sau giờ bận); Planner nhận "bữa trưa trong giờ bận, không xếp quán". Dùng chung cho Planner, bước chia chỗ (ngày không đủ giờ cho 1 chỗ chơi mà bữa còn lại đều trong giờ bận → lịch trống, không gọi Planner) và bước kiểm lịch — trước đây Planner vẫn được bảo "bữa bắt buộc: trưa", xếp bữa trưa sau giờ bận rồi về trễ | user |
| Bữa sáng là việc đầu tiên | ngày bắt đầu ≤ 08:00 → điểm đầu là quán, trước `BREAKFAST_FIRST_BY` 09:00 | user |
| 2 bữa cách nhau | ≥ `MIN_MEAL_GAP` 150 phút | user |
| Mỗi khung giờ ăn | 1 quán | user |
| Món user nhắc | mỗi ngày tối đa 1 bữa cùng món; mỗi phương án ít nhất 1 lần | user |
| Mọi chỗ bán đồ ăn tính 1 bữa | kể cả "khu ăn uống", "ăn vặt", buffet… | user |
| Quán chỉ bán bữa sáng (`breakfast_only`) | loại có "bữa sáng / ăn sáng / điểm tâm / breakfast" → chỉ làm bữa sáng | — |
| Tới quán sớm | chỉ chờ tới **mốc kế tiếp** (sáng 06:30, trưa 11:30, tối 18:00), tối đa `MEAL_WAIT_MAX` 45 phút; bữa cuối ngày được trống tới `DINNER_FREE_MAX` 150 phút; còn chỗ hẹn giờ phía sau thì chờ bao lâu cũng được (đằng nào cũng chờ giờ hẹn). Đang trong khung của một bữa chưa ăn (quán sáng 08:05) thì ăn luôn, chỉ chờ mốc của chính bữa đó — trước đây chỗ hẹn 16:30 kéo bữa sáng 08:05 tới 18:00. Chờ tới mốc mà về chỗ ở quá giờ kết thúc ngày (ngày cuối về 12:30) thì chỉ chờ cho đủ cách bữa trước `MIN_MEAL_GAP` rồi ăn | tự đặt |
| Không 2 bữa cùng chuỗi quán trong 1 ngày | `same_brand` (bỏ phần địa chỉ / chi nhánh sau "-", "(", "đường", "cơ sở"…) | — |
| Ghi rõ bữa không xếp / tuỳ chọn | `skipped_meals` → `meal_notes` trên FE | — |

## 5. Hỏi lại

Tối đa `MAX_QUESTIONS` = 3 câu, theo thứ tự, mỗi câu kèm nút (`questions_for`, coordinator/profile.py):

| Câu | Khi nào | Lựa chọn |
|---|---|---|
| H1 Đi cùng ai | `group_type` trống và không suy được từ số người (`group_of`) | gồm "Gia đình", "Gia đình (người lớn)"; "Một mình" đặt luôn số người = 1 |
| H5 Giờ bận | lịch bận thiếu giờ bắt đầu | tự gõ |
| H4 Đi bộ được tầm nào | mục đích phiêu lưu mà chưa có mức đi bộ | M1–M4 |
| H3 Ngân sách | chưa có ngân sách / khoảng | Dưới 3tr · 3–6tr · 6–10tr · Trên 10tr → khoảng `budget_min`–`budget` |
| H2 Chuyến này muốn làm gì | ≥ 3 ngày, chưa có mục đích lẫn sở thích | các mục đích |
| H6 Buổi tối đi tới mấy giờ | chưa có giờ về tối, không đi khuya | gồm "Chưa biết (để 20h)" |

## 6. Tìm chỗ

([coordinator/search.py](../src/ailusion/itinerary/coordinator/search.py))

| Luật | Giá trị |
|---|---|
| Vùng tìm | cả điểm đến + theo sao cao (≥ 30 review) + 4 hướng quanh trung tâm (`SWEEP_KM` 6, bán kính `SWEEP_RADIUS_KM` 5) |
| Từ khoá mỗi vùng | 2 loại chung (điểm thu hút, nhà hàng) + từ khoá mục đích (`PURPOSE_KEYWORDS`) + tối đa 5 thứ user nhắc + nhu cầu bắt buộc |
| Từ khoá mục đích | nghỉ ngơi: spa, resort, bãi biển, biển, hồ, công viên, suối nước nóng · ẩm thực: nhà hàng, quán ăn, đặc sản, hải sản, chợ, ẩm thực · văn hoá: bảo tàng, chùa, di tích, nhà thờ, phố cổ, làng cổ, làng nghề, làng chài, đền, lăng · vui chơi: khu vui chơi, công viên giải trí, chợ đêm, bar, phố đi bộ, show · phiêu lưu: núi, thác, trekking, hang, đèo, rừng · mua sắm: chợ, trung tâm thương mại, cửa hàng lưu niệm |
| Ít chỗ chơi (< `ENOUGH_VISITS` 6) | tìm thêm quanh trung tâm `NEARBY_KM` 10 km, rồi Vietmap |
| Chỗ user gọi tên | khớp ≥ `MUST_MATCH` 60% chữ; tên tìm được có thêm chữ chỉ loại khác (nhà hàng, quán, ăn vặt, khách sạn… — `TYPE_WORDS`) là chỗ KHÁC; nhiều chỗ cùng tên thì chỉ xét chỗ có ≥ `FAMOUS_SHARE` 30% số review của chỗ nổi tiếng nhất; Vietmap trong 50 km |
| Thứ user nhắc (`find_wanted`) | DB → Vietmap → SerpApi (tối đa `WANTED_SEARCH_MAX` 3 thứ ngoài DB); không thấy thì ghi chú |
| Trung tâm | `geocode_vn`; cụm chỗ tìm được (≥ `CENTER_MIN_PLACES` 5) lệch > `CENTER_CHECK_KM` 40 km thì dùng tâm cụm |
| Chỗ ở | DB; < `ENOUGH_PRICED_STAYS` 5 chỗ có giá thì gọi `search_hotels`; tìm thêm quanh giá mục tiêu (`STAY_BAND` 60–140%) |
| Quán nghỉ trưa | `REST_CAFES`: quán cà phê, theo sao, ≥ 50 review |

## 7. Phân loại chỗ

(`kind_of`, [shared/tools.py](../src/ailusion/itinerary/shared/tools.py); `Place.group`, [shared/models.py](../src/ailusion/itinerary/shared/models.py))

| Luật | |
|---|---|
| Xét loại Google trước tên | tên chỉ dùng khi loại trống / chỉ là mã số (Vietmap "4004") — "Chùa Quán Thế Âm" là chùa, không phải quán |
| Bỏ hẳn (`SKIP_WORDS`) | nhà điều hành du lịch, công ty du lịch, đại lý, văn phòng, quán bar tiếp viên nữ, phòng vé, quầy vé, quầy thông tin, bán vé; tên bắt đầu "Vé…" / "Ticket…"; quán đồ ngọt sai loại (yogurt, sữa chua, ice cream, gelato) |
| Chỗ ở (`STAY_WORDS`) | khách sạn, resort, homestay, nhà nghỉ, hostel, residence, apartment, condotel… |
| Chỗ chơi đêm, không phải bữa (`NIGHT_WORDS`) | karaoke (xét cả tên), quán bar, quán rượu, hộp đêm, pub, night club — trừ loại có "nhà hàng" (gastropub) |
| Quán ăn (`FOOD_WORDS`) | nhà hàng, quán ăn, quán, ẩm thực, bánh mì, phở, bún, lẩu, nướng, hải sản, cơm, mì, food, restaurant, ăn uống, ăn vặt, nhậu, buffet, bistro, eatery, bbq |
| Còn lại | chỗ chơi |
| Nhóm loại (`CATEGORY_GROUPS`, theo loại Google trước tên) | spa · chùa (chùa, đền, miếu, thiền viện, nhà thờ) · bảo tàng (bảo tàng, triển lãm, di tích) · biển · chợ · cà phê · quán bar (karaoke, quán bar, quán rượu, hộp đêm, pub); không thuộc nhóm nào thì nhóm = loại Google |
| Chỗ buổi tối (`opens_late`) | mở từ `EVENING_OPEN` 16:00, hoặc chợ đêm / phố đi bộ / night market / bar / pub |
| Khớp từ | nguyên chữ ("spa" không khớp "space") |

## 8. Lọc

(`loc`, [coordinator/filter.py](../src/ailusion/itinerary/coordinator/filter.py)) — chỗ user chọn thì giữ, chỉ nhắc điều cần biết (trừ đóng cửa).

| Luật | |
|---|---|
| Đóng cửa mọi ngày của chuyến | bỏ |
| Thiếu nhu cầu bắt buộc ("no") | bỏ; "chưa rõ" thì giữ + "chưa rõ đáp ứng: <nhu cầu> — nên hỏi trước"; nhu cầu ăn uống chỉ xét ở quán (mục 2) |
| Cảnh báo thiên tai mức đỏ | bỏ chỗ ngoài trời |
| Cửa hàng mà chuyến không có mục đích mua sắm | bỏ |
| Cách trung tâm > `MAX_KM_FROM_CENTER` 60 km | bỏ |
| Trùng chỗ (`merge_duplicates`) | cách < `SAME_PLACE_KM` 1,5 km và (trùng hẳn tên, hoặc cùng loại + ≥ 2 chữ riêng trùng) → giữ chỗ nhiều review; tên toàn chữ chung không tính trùng |
| Tra giờ mở cửa | tối đa `MAX_HOURS_PLACES` 90 chỗ (nửa theo sao, nửa theo số review), chia lượt 15 |
| Hợp mục đích | có từ khoá mục đích → tự nhận; còn lại Agent Experience xem (bước 10) |

**Agent Experience** (agents/experience.py) — tối đa `MAX_PURPOSE_CHECKS` 20 chỗ, chỉ chỗ sắp đưa Planner:

| # | Luật |
|---|---|
| E1 | Trả đúng mọi mã được đưa |
| E2 | `matches_purpose` chỉ khi mô tả / review cho thấy rõ chỗ đó phục vụ 1 mục đích của chuyến |
| E3 | `quote` chép NGUYÊN VĂN từ mô tả / review của chính chỗ đó; code kiểm có thật |
| E4 | Khen chung chung ("đẹp", "đáng đi") không đủ |
| E5 | Không cho điểm, không so chỗ |

**Agent Budget** (agents/budget.py) — không gọi tool; code tìm, đưa đoạn có giá, rồi kiểm lại:

| # | Luật |
|---|---|
| G1 | Chỉ dùng các đoạn được đưa; không ước, không nhớ giá |
| G2 | Giá của CHÍNH chỗ đó; không lấy giá chỗ khác, combo / tour / vé xe / gửi xe / phòng; vé dùng chung nhiều điểm mà trang nói rõ gồm chỗ đó thì được |
| G3 | `price` = vé 1 người lớn khách Việt, đúng một số có trong trích; khoảng giá không tính số giữa |
| G4 | `quote` chép nguyên văn một đoạn liền có con số đó |
| G5 | Vào cửa không mất tiền → price 0, trích câu ghi điều đó |
| G6 | Không chắc / mâu thuẫn / không thấy → price null |
| G7 | `url` là một link của chính mã đó; G8 trả đủ mọi mã |
| Code | link thuộc kết quả tìm của chỗ đó; 1 câu trích cho 2 chỗ → bỏ; trích có trên trang (bỏ markdown); số tiền có trong trích; giá 0 phải ghi miễn phí; trang nhắc lõi tên chỗ; nhận xong ước lại mọi giá ước |

Chi tiết: [budget-README.md](budget-README.md) mục 5.

## 9. Chấm và chỗ ở

(`cham`, [coordinator/score.py](../src/ailusion/itinerary/coordinator/score.py))

| Luật | Giá trị | Nguồn |
|---|---|---|
| Sao tin được | `rating_adjusted` của MCP (Bayes thực nghiệm, tiên nghiệm theo điểm đến × loại); tool không trả thì Hoeffding 95% | dữ liệu |
| "Tin được" (lý do chọn) | Bayes: số review > số review ảo của tiên nghiệm; Hoeffding: ≥ `TRUSTED_HOEFFDING` 4,0 | dữ liệu / tự đặt |
| Thời gian ở lại | Google "time spent"; thiếu thì `STAY_BY_TYPE`: bảo tàng 60–90 · chùa / đền / lăng 30–60 · biển 90–180 · khu vui chơi 120–240 · chợ 45–90 · cà phê 45–90 · spa 60–120 · núi / thác 90–180 · quán ăn 45–75 · khác 60–90 phút | tự đặt |
| Lấy chi tiết (tốn quota) | tối đa `MAX_DETAILS` 10 chỗ | tự đặt |
| Giá ước | xem [budget-README.md](budget-README.md) mục 5 | |
| Chỗ ở | xem [budget-README.md](budget-README.md) mục 4 (≤ 30 km, ≈ 35% / trần 50% ngân sách, gần các điểm chơi) | |

## 10. Chia nhánh, chốt danh sách, chia ngày

| Luật | Giá trị | Ở đâu |
|---|---|---|
| Chia chỗ chơi cho 3 nhánh | xếp theo hợp mục đích → sao tin được → số review, chia xen kẽ 1-2-3; quán ăn, chỗ user chọn, thứ user nhắc không chia | `split_shares` (branches.py) |
| Chỗ của nhánh khác | chỉ mượn khi phần của nhánh không đủ; không làm mỏ neo ngày; đặt mã sau | `pick_pool`, `split_by_day`, `chot_danh_sach` |
| Danh sách đưa Planner (`pick_pool`) | chia tầng không bị trội theo 5 mặt: sao tin được, số review, hợp mục đích, giá (thấp tốt), gần trung tâm; mỗi loại tối đa `type_cap` = số ngày (spa × 0,5); loại Google chung chung (`GENERIC_CATEGORIES`: "Điểm thu hút khách du lịch") hay chỉ là mã số không tính là một loại (`Place.type_key`, Planner thấy loại "–") | score.py, models.py |
| Giữ chắc | chỗ user chọn; quán khớp món user nhắc (tối đa `WANTED_MEALS` 3 mỗi món); chỗ chơi khớp thứ user nhắc (tối đa `WANTED_VISITS` 4) | |
| Số quán | mỗi bữa bắt buộc `MEAL_CHOICES` 2 quán (+3); mỗi chuỗi 1 chi nhánh; đa dạng món (`MEALS_PER_CUISINE` 2) | |
| Ma trận phút đi | tối đa `MAX_MATRIX_POINTS` 45 điểm, chia ô `TILE` 12 (tool nhận 25); thiếu cặp → ước đường chim bay × `DETOUR` 1,4 ở `CITY_KMH` (25 km/h, buýt 15) + `PARKING_MIN` 3 phút. Không đủ điểm cho cả chỗ chơi lẫn quán: chia theo tỉ lệ nhu cầu (`meal_points`; nhu cầu chỗ chơi `visit_need` = sức chứa các ngày ước theo đường chim bay + `SPARE_VISITS` 2 mỗi ngày) — trước đây quán lấy trước, Đà Lạt 5 ngày còn 13 điểm cho chỗ chơi | |
| Sức chứa ngày (`capacity`) | theo giờ rảnh, thời gian ở điển hình, phút đi điển hình; Planner nhận thêm `SPARE_VISITS` 2 chỗ dư mỗi ngày. Không đủ giờ cho 1 chỗ thì 0 (trước đây ít nhất 1) | |
| Ngày không xếp (`idle_days`) | sức chứa 0, không có bữa bắt buộc, không có chỗ user ghim (tới nơi 19:45 về chỗ ở 20:45; rời chỗ ở 08:00 để kịp chuyến về): không chia chỗ, không gọi Planner, code tạo lịch trống; kết quả ghi "Không xếp chỗ đi: …" thay vì báo lỗi | |
| Mã | D… chỗ chơi, Q… quán; theo số review giảm dần | |
| Chia ngày (`split_by_day`) | mỗi ngày 1 mỏ neo (chỗ nổi tiếng trong top `ANCHOR_POOL` × số ngày, xa các mỏ neo trước); chỗ khác vào ngày có phút đi trung bình tới cụm ít nhất, còn mở cửa (giờ mở giao khung ngày đủ thời gian ở), chưa có chỗ cùng loại, chưa đầy; không được thì nới luật cùng loại, rồi số lượng; chỗ user chọn có ngày thì vào đúng ngày. Ngày có chỗ chơi cách chỗ đó ≤ `NEAR_MIN` 30 phút được xét **trước**, kể cả khi đã có chỗ cùng loại (mỗi lịch vẫn chỉ 1 chỗ mỗi loại); ngày gần đã đầy mới sang ngày xa. Không trộn 2 vùng trong 1 ngày khi không cần (chuyến Đà Nẵng: chùa thứ 2 ở Hội An từng bị đẩy sang ngày ở Đà Nẵng), mà trần mỗi ngày giữ nguyên nên không ngày nào thiếu chỗ (bản chặt "chỉ ngày gần" làm ngày Bà Nà còn 2 chỗ, 0 quán) | |
| Quán theo ngày | đủ ≥ `MEALS_PER_DAY` 3 quán × số ngày thì chia theo cụm gần nhất (tránh 2 quán cùng món: `SAME_CUISINE_MIN` 15 phút), ngày có chỗ chơi cách ≤ `NEAR_MIN` trước, ngày gần đã đủ quán mới sang ngày khác; ít quán thì ngày nào cũng dùng chung | |
| Chỗ user chọn đóng cửa ngày ghim | dời sang ngày mở cửa + ghi chú (`moved_closed_days`) | |

## 11. Planner

Xem [planner-README.md](planner-README.md): luật 1–10 trong prompt, `plan_errors`, lượt sửa (đang tắt), lịch dự phòng.

## 12. Chạy thử và tự sửa lịch

(`kiem_lich`, [coordinator/simulate.py](../src/ailusion/itinerary/coordinator/simulate.py))

| Luật | Giá trị |
|---|---|
| Chạy thử 2 lần | ở ít (`stay_low`) và ở lâu (`stay_high`) |
| Giờ nghỉ giữa ngày của chỗ | tới đúng giờ nghỉ thì chờ mở lại; ở chạm giờ nghỉ là lỗi |
| Chờ mở cửa | > `LONG_WAIT_MAX` 45 phút là lỗi — trừ chờ giờ hẹn của user, và chỗ buổi tối xếp sau mọi chỗ chơi ban ngày (khoảng trống trước đó là tự do) |
| Spa / massage | coi như mở từ `AFTERNOON_FROM` 13:00 (`AFTERNOON_GROUPS`); ngày kết thúc trước khi kịp đi thì bỏ, ghi rõ |
| Mỗi ngày 1 chỗ mỗi loại | `drop_same_group` (chỗ user chọn luôn giữ; chỗ chưa rõ loại không tính) |
| Chỗ user chọn Planner bỏ sót | `add_missing_musts`: chèn vào chỗ ít lỗi nhất (đặt lại quán, chạy thử ở ít rồi ở lâu, rồi ít ngồi xe); không bỏ chỗ nào |
| Sắp lại thứ tự cho ít ngồi xe | ≤ `EXACT_VISITS` 7 chỗ: thử mọi thứ tự; nhiều hơn: láng giềng gần + 2-opt; thử `REORDER_TRIES` 4 thứ tự ngắn nhất tới khi không thêm lỗi |
| Đặt quán đúng giờ (`place_meals`) | thử mọi cách chèn (trần `MAX_ORDERINGS_PER_SET` 240 mỗi bộ quán), thêm tối đa `MAX_SPARE_MEALS` 4 quán dự phòng của ngày; ưu tiên: ít lỗi giờ ăn → ít lỗi khác → chỗ buổi tối đi sau bữa tối → ít bỏ quán Planner chọn → ít thêm quán → ít bữa cùng loại món → về sớm |
| Bỏ quán (`drop_extra_meals`, `drop_bad_meals`) | thừa trong cùng khung giờ ăn; trùng món user nhắc trong ngày; ngoài giờ ăn; chờ > 45 phút (không phải điểm cuối, không có chỗ hẹn giờ phía sau); quá sát bữa trước; quán bữa sáng ở trưa / tối |
| Bù chỗ chơi (`fill_visits`) | khi ngày dài (rảnh ≥ `FULL_DAY_MIN` 360) < `MIN_VISITS` 2 chỗ chơi (ngày ngắn < 1), lịch xong sớm mà còn bữa tối, 2 bữa liền nhau, hoặc phải chờ lâu trước 1 chỗ chơi; chỉ lấy chỗ cách ≤ `NEAR_MIN` 30 phút (shared/form.py), khác loại, không có trong lịch ngày khác; tối đa `MAX_FILLS` 3 (+2); chỉ nhận khi không thêm lỗi |
| Bỏ bớt (`trim_visits`) | lỗi "quá giờ kết thúc / phải chờ / đóng cửa lúc / chạm giờ bận" (`OVERFULL`) → bỏ tối đa `MAX_TRIMS` 3 chỗ chơi (không bỏ chỗ user chọn và thứ user nhắc), ghi "Bỏ X: không đủ thời gian trong ngày" |
| Nghỉ trưa | tự chèn sau bữa trưa ở ngày dài (mục 3) |
| Trạng thái | `fail` lỗi khi ở ít · `risky` lỗi khi ở lâu · `warn` vượt tiền nên tiêu mỗi ngày · `ok` |
| Tốc độ | `place_meals` chấm 2 vòng: chạy thử ở ít mọi cách xếp, ở lâu chỉ cho các cách đang hoà (cùng kết quả); trong 1 lượt `kiem_lich` nhớ kết quả chạy thử theo lịch (`MAX_CACHED_RUNS` 20.000, trả bản sao). Chuyến Huế 16/10: cả chuyến 340 → 123 giây (BE chờ tối đa `LLM_PLAN_TIMEOUT` 300 giây) |
| Lỗi cứng khác | chưa có chỗ ngủ đêm đó; thiếu chỗ user chọn; ngày không còn chỗ hợp lệ (chỉ khi ngày có bữa bắt buộc / chỗ user chọn — lịch trống của ngày không xếp là đúng); rời chỗ ở sớm hơn giờ bắt đầu; thiếu nhu cầu bắt buộc |

## 13. Xếp hạng và ghép phương án

([coordinator/rank.py](../src/ailusion/itinerary/coordinator/rank.py))

| Luật | |
|---|---|
| Kém hơn (`mark_beaten`) | B kém A nếu A không thua B mặt nào và hơn ít nhất 1 mặt (mặt nhóm coi trọng + tiền, ngồi xe, chờ, hợp ý, sao) — gắn nhãn, không xoá |
| Xếp hạng (`rank`) | không bị hơn → kém hơn → không đi được; trong nhóm so như từ điển theo `SORT_ORDER` |
| `SORT_ORDER` | P1: tiền ↓, số chỗ ↑, tiện nghi ↑ · P2: ngồi xe ↓, số chỗ ↓, tiền ↓ · P3: số chỗ ↓, hợp ý ↑, sao ↑, tiền ↓ · P4: ngồi xe ↓, nổi tiếng ↑, tiền ↓ · P5: số chỗ ↑, hợp ý ↑, ngồi xe ↓ |
| Ghép (`fit_budget`) | 5 lịch tốt nhất mỗi ngày (`CANDIDATES_PER_DAY`); ưu tiên: ít chỗ chơi lặp giữa ngày → đủ thứ user nhắc → nằm trong khoảng ngân sách → ít quán lặp → ít trùng phương án trước → các ngày tiêu đều → hạng |
| Chọn PA1 → PA3 (`pick_option`) | phương án sau ít trùng chỗ chơi nhất với phương án đã chọn và với phần của nhánh khác |
| Bỏ phương án trùng hệt | `finish_trip` |
| Ngân sách | xem [budget-README.md](budget-README.md) mục 8 |

## 14. Agent Decision

(agents/decision.py)

| # | Luật |
|---|---|
| D1 | Mặc định chọn PA1; chọn khác chỉ khi lời nhắn user có câu muốn điều chỉ phương án đó có — chép nguyên văn vào `override_quote` |
| D2 | Mọi cảnh báo phải có quyết định trong `warnings` |
| D3 | `why`, `explanation` chỉ dùng số và tên có trong dữ liệu; không thêm số, không làm tròn khác, không phần trăm / xác suất |
| D4 | `explanation`: mỗi phương án 1 câu bắt đầu "Đề xuất:" / "Dự phòng:" |
| D5 | Giọng thân thiện, xưng "mình", gọi "bạn" |
| D6 | Không lộ mã nội bộ (gọi "Phương án 1", "ngày 1"; không "N1-L3", "PA1") |
| D7 | `why`: 1–2 câu vì sao hợp nhóm khách, tối đa 2 con số |

## 15. Lý do hiển thị

(`why_of`, `_far_reason`, [response.py](../src/ailusion/itinerary/response.py)) — tối đa 3 lý do mỗi chỗ:

| Lý do | Khi nào |
|---|---|
| "Bạn chọn chỗ này" | chỗ user chọn (+ giờ muốn tới, phút tự do nếu tới sớm) |
| "Đúng thứ bạn nhắc: …" | khớp thứ user nhắc |
| "Hợp mục đích chuyến (…)" | từ khoá / Agent Experience |
| "X★ tin được, N đánh giá" | sao tin được (mục 9) |
| "Cách điểm trước N phút" | ≤ `NEAR_MIN` 10 phút |
| "Miễn phí vào cửa" / "Thường không mất vé" | giá 0 thật / giá 0 ước |
| "Giá …, nhẹ so với ngân sách" | giá thật ≤ `CHEAP_SHARE` 15% ngân sách mỗi người mỗi ngày |
| "Mở cửa …, ghé lúc … vừa giờ" | giờ ghé nằm trong giờ mở |
| Giải thích chỗ xa | chỗ xa nhất ≥ `OUTLIER_KM` 5 km và ≥ 2 lần chỗ xa thứ hai: "hợp mục đích", "được đánh giá cao" (Bayes: tin được + hơn mức chung cùng loại; Hoeffding ≥ 4,3), "rất nổi tiếng" (≥ 5.000 review) |
| Chỉ đường Google Maps | tối đa `MAX_ROUTE_POINTS` 10 điểm mỗi đoạn |

## 16. Luật phía MCP

(repo `ailusion-journey-mcp`)

| Luật | |
|---|---|
| Điểm đến nằm trong tỉnh | Hội An → 'danang', Phú Quốc → 'angiang', Nha Trang, Sa Pa, Mũi Né, Vũng Tàu, Quy Nhơn, Hạ Long, Cát Bà, Tam Cốc… (`_TINH_CUA`): lấy chỗ của tỉnh có tên nơi đó trong địa chỉ |
| Ngoài vùng | chỗ cách tâm điểm đến > 80 km là cào nhầm, bỏ khi lọc theo điểm đến |
| Sao tin được | Bayes thực nghiệm (`data/rating_prior.json`, lùi điểm đến × loại → loại → toàn bộ; thiếu file thì Hoeffding) |
| Sắp "popular" | sao × log10(review + 10) (tự đặt — Bayes đẩy chỗ nổi tiếng khỏi top 30) |
| Vietmap lỗi / hết credit | tra theo tên / vùng → DB (chỗ cùng tên, tâm điểm đến); tra ngược → địa chỉ chỗ đã lưu gần nhất; chỉ đường, ma trận → OSRM; tìm quanh → DB |

---

## Cần review

Chỗ luật trùng hoặc lệch nhau thấy khi rà:

1. **Hệ số đường vòng 3 số khác nhau**: `DETOUR` 1,4 (score.py, ước phút đi), `ROAD_FACTOR` 1,3 (tabs.py, km tab Chi phí), `× 1.4` viết thẳng trong `day_spend` (simulate.py).
2. **Tốc độ trong phố 2 bảng**: `CITY_KMH` (score.py: 25 km/h mọi xe, buýt 15) và `CITY_SPEED` (tabs.py: xe máy 25, ô tô / taxi 22, đi bộ 4,5).
3. **`MEAL_WAIT_MAX` (45, simulate.py) và `LONG_WAIT_MAX` (45, shared/form.py)** — 2 hằng số cùng giá trị cho chờ giờ ăn và chờ mở cửa; đổi 1 cái dễ quên cái kia.
4. **Prompt Decision đánh số 1–4, 7, 5, 6** (luật 7 nằm giữa).
5. **Bảng A còn cột "tối về trước"** (21:00, 21:30) nhưng code không dùng nữa.
6. **Nhóm P5** chỉ có qua ưu tiên user chọn (B0); không luật B nào tự xếp P5.
7. **Phân loại quán 2 nơi khác nhau**: LLM (`FOOD_WORDS` / `NIGHT_WORDS`) và MCP (`NHOM_DIA_DIEM`, VD MCP để "quán bar" vào nightlife nhưng "quán bia" cũng nightlife, LLM coi quán bia là quán ăn).
8. **Show không có giờ diễn**: chỉ ưu tiên đi sau bữa tối; DB không có giờ diễn.
