Bạn chọn khái niệm HPO (Human Phenotype Ontology) cho các biểu hiện đã được tách từ một mô tả khám thai do bác sĩ viết bằng tiếng Việt. Mỗi mục có `phrase` (biểu hiện), assertion (có, không có, nghi, chưa kết luận được), subject (thai, mẹ, tiền sử gia đình), câu nguồn trong mô tả, và một danh sách ứng viên HPO do hệ thống tìm được cho riêng mục đó. Mã được chọn dùng để tra bệnh và gene liên quan.

# Tiêu chí

- Với mỗi mục, chọn **một** mã trong các ứng viên **của chính mục đó**, diễn đạt đúng biểu hiện bác sĩ mô tả. Không dùng mã ngoài danh sách, không dùng ứng viên của mục khác.
- Đọc mô tả gốc và câu nguồn để hiểu đúng ngữ cảnh: cơ quan, bên, tổn thương đang nói tới. `phrase` có thể là cách gọi lâm sàng khác với tên trong HPO; xét theo nghĩa, dùng nhãn tiếng Anh, tên tiếng Việt và định nghĩa của ứng viên.
- Chọn term **cụ thể nhất mà mô tả đủ căn cứ**. Không chọn term cụ thể hơn điều bác sĩ viết. Nếu chỉ có term chung hơn đúng nghĩa thì chọn term chung đó.
- Mục ABSENT chọn term của bất thường được nhắc tới. Trạng thái nằm ở assertion, không làm đổi term.
- Mục UNCERTAIN chỉ được gán term khi `phrase` nêu một bất thường cụ thể. Mục UNCERTAIN chỉ nêu một cấu trúc chưa khảo sát được hoặc khảo sát hạn chế nhận `UNMAPPED`, vì gán term chung sẽ biến nó thành bất thường hoặc bình thường của cấu trúc đó trong khi bác sĩ chưa kết luận.
- `AMBIGUOUS` khi mô tả không đủ để phân biệt hai hay nhiều ứng viên khác nghĩa và trong ứng viên không có term chung nào đúng nghĩa; trả các mã còn phân vân.
- `UNMAPPED` khi không ứng viên nào đúng nghĩa.
- Mục của mẹ hoặc tiền sử gia đình chọn theo cùng tiêu chí.
- Mỗi mục được gửi phải có **đúng một** câu trả lời.

# Cách trả lời

Mỗi mục một phần tử trong `choices`:
- `index`: số trong ngoặc vuông của mục.
- `decision`: `MAPPED`, `AMBIGUOUS` hoặc `UNMAPPED`.
- `hpo_id`: mã đã chọn, chỉ khi `MAPPED`.
- `options`: các mã còn phân vân (ít nhất 2), chỉ khi `AMBIGUOUS`.

# Ví dụ minh hoạ tiêu chí

- `xương đòn ngắn` (PRESENT), ứng viên có *Short clavicles* và *Abnormal clavicle morphology* → `MAPPED` *Short clavicles*. Nếu `phrase` chỉ là `bất thường xương đòn` thì chọn *Abnormal clavicle morphology*, không chọn *Short clavicles*.
- `bất thường xương bả vai` (ABSENT) → `MAPPED` *Abnormal scapula morphology*; assertion ABSENT đã nói là không có.
- `khớp háng` (UNCERTAIN, khảo sát hạn chế) → `UNMAPPED`, dù có ứng viên *Abnormal hip joint morphology*. Còn `trật khớp háng` (UNCERTAIN) nêu một bất thường cụ thể → `MAPPED` *Hip dislocation*.
- `khối vùng cổ trước` (PRESENT), câu nguồn không nói khối đặc hay nang, ứng viên có *Goiter* và *Thyroglossal cyst* nhưng không có term chung nào đúng nghĩa → `AMBIGUOUS` với hai mã đó.
- `tiền sản giật` (MOTHER, PRESENT) → `MAPPED` *Preeclampsia*.
