# Việc còn mở

Ghi lại để làm sau (17/10/2026), chưa làm. Mỗi việc: lời user, hiện trạng code, việc cần làm rõ.
Liên quan cả LLM (repo này) lẫn FE (`ailusion-journey-fe`).

## 1. Chỉ lên plan từ vị trí đi chơi trở đi

- **Lời user:** "note lại phần chỉ lên plan từ vị trí đi chơi trở đi".
- **Hiện trạng:** lịch mỗi ngày bắt đầu bằng "Xuất phát từ <chỗ ở>" và kết thúc bằng "Về <chỗ ở>"
  (ngày cuối đi thẳng từ chỗ bận ra bến thì là mục `depart`). Ngày 1 bắt đầu từ giờ tới bến + ra khỏi
  bến (`HUB_EXIT_MIN` 15 phút) + bến → chỗ ở (`HUB_TRAVEL_MIN` 30 phút); lộ trình không gồm chặng từ
  nơi user ở tới điểm đến.
- **Cần làm rõ:** lên plan bắt đầu từ chỗ nào — từ chỗ đi chơi đầu tiên (bỏ dòng xuất phát / về chỗ
  ở), hay từ chỗ user đang đứng giữa chuyến (lập lại phần còn lại của ngày)?

## 2. Issue 1: đổi địa điểm sau khi chọn lên plan mặc định thì có cảnh báo

- **Lời user:** "nếu đổi địa điểm sau khi chọn lên plan defaut thì sẽ có warning".
- **Hiện trạng (FE `trip-conflicts.ts`):** form so với điều trợ lý chat đã ghi (`TripInfo`); khác
  điểm đến, số ngày hay ngày đi thì hiện "N chỗ chưa khớp — chọn giúp mình trước khi lập lộ trình",
  VD "Trong chat bạn nói đi “Đà Nẵng”, form đang ghi “Nha Trang”. Lập lộ trình cho nơi nào?", và nút
  "Lập lộ trình" bị khoá tới khi chọn. User sửa ô "Điểm đến" trên form (form là ý mới nhất) vẫn bị hỏi
  lại.
- **Cần làm rõ:** bỏ hẳn cảnh báo khi user tự sửa trên form, hay vẫn nhắc nhưng không khoá nút?

## 3. Yêu cầu bổ sung nên gợi ý theo độ tuổi

- **Lời user:** "các yêu cầu bổ sung nên gợi ý theo độ tuổi".
- **Hiện trạng:** ô "Nhu cầu bắt buộc" luôn hiện đủ 6 chip (có món chay, thuần chay, lối xe lăn, ghế
  cao cho bé, chỗ đỗ ô tô, hợp trẻ em) cho mọi đoàn. Bảng A (`TABLE_A`) đã tự thêm nhu cầu "mong có"
  theo độ tuổi nhưng user không thấy: bé dưới 6 → hợp trẻ em, ghế cao, bàn thay tã; trẻ 6–12 → hợp
  trẻ em, sân chơi; từ 65 → có nhà vệ sinh; xe lăn → lối vào, nhà vệ sinh cho xe lăn (bắt buộc). FE
  chỉ hỏi khi chip lệch số người (VD chọn "Ghế cao cho bé" mà không có bé: "Bỏ ghế cao cho bé" / "Có
  1 bé dưới 6 tuổi").
- **Hướng làm:** nhập số bé / người lớn tuổi / xe lăn thì gợi ý chip hợp độ tuổi lên đầu (VD có bé
  dưới 6: ghế cao cho bé, hợp trẻ em, bàn thay tã; người từ 65: có nhà vệ sinh, lối xe lăn, ít đi
  bộ), kèm gợi ý cho ô "Nhu cầu khác" (VD "bé dị ứng …", "ông bà đi lại khó").
