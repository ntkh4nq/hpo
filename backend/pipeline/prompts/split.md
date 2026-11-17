Bạn đọc mô tả khám thai (siêu âm, MRI, ghi chú khám) do bác sĩ viết bằng tiếng Việt và tách thành danh sách các biểu hiện. Danh sách này dùng để tra từng biểu hiện sang HPO (Human Phenotype Ontology), nên mỗi mục phải tra được độc lập.

# Một mục là gì

- Một mục là **một biểu hiện**: một bất thường, hoặc một kết luận không có bất thường, ở một cấu trúc, ứng với **một khái niệm HPO**. Cách thử: đem riêng cụm đó đi tra thì ra đúng một khái niệm.
- Cụm chứa hai khái niệm thì tách thành hai mục. Phần nào tách ra bị mất nghĩa (mô tả bổ nghĩa cho cùng một khái niệm) thì giữ trong cùng mục.
- Một nhận xét áp cho nhiều cấu trúc thì mỗi cấu trúc một mục. Nhiều nhận xét cho cùng một cấu trúc thì mỗi nhận xét một mục.
- Câu kết luận bình thường ("X bình thường", "chưa thấy bất thường X", "X liên tục") cũng là một mục, với assertion ABSENT.
- Khi chính việc **không thấy một cấu trúc** là bất thường (ví dụ "không thấy xương mũi"), đó là biểu hiện có (PRESENT) và `phrase` giữ chữ "không thấy".
- Một biểu hiện được nhắc nhiều lần (mô tả một tổn thương rồi gọi tên chính tổn thương đó, hoặc nhắc lại ở chỗ khác) vẫn là một mục, theo kết luận của bác sĩ. Các biểu hiện khác nhau được nêu làm căn cứ cho một kết luận thì mỗi cái vẫn là một mục.
- Con số đo không kèm chữ kết luận (ví dụ "chiều dài xương đùi 32 mm") không phải mục: bác sĩ chưa kết luận gì, tự diễn giải con số là thêm thông tin không có trong mô tả. Có chữ kết luận ("dưới bách phân vị 3", "tăng") thì là mục.
- Không đưa vào danh sách: gene và biến thể, kết quả xét nghiệm (máu, sàng lọc, di truyền, chọc ối), nhóm máu, tuổi, giới tính, nhịp tim ghi bằng số, chẩn đoán bệnh hay hội chứng di truyền (ví dụ trisomy 21, hội chứng Apert) vì đó là kết luận về nguyên nhân, không phải biểu hiện. Tên một dị tật hay bất thường hình thái (ví dụ dị tật Chiari, loạn sản thận) là biểu hiện, vẫn là mục.
- Chỉ lấy kết luận của lần khám hiện tại. Biểu hiện lần trước có mà nay đã hết thì ghi ABSENT.

# Trường `phrase`

- **Tự đủ nghĩa**: chỉ đọc `phrase` vẫn biết đó là biểu hiện gì, ở cấu trúc nào. Thông tin dùng chung đặt ở chỗ khác trong câu (cơ quan, vị trí, bên trái/phải/hai bên) được đưa vào `phrase`.
- **Dùng đúng chữ bác sĩ viết.** Không dịch, không đổi sang thuật ngữ khác, không thêm chữ bác sĩ không viết.
- `phrase` là **thứ được kết luận**, còn kết luận nằm ở assertion và assessment. Phép thử: ghép assertion với `phrase` ("có …", "không có …", "nghi …", "chưa rõ …") phải nói lại đúng ý bác sĩ. Vì vậy `phrase` là tên một bất thường; khi bác sĩ kết luận một cấu trúc bình thường thì `phrase` là "bất thường" + tên cấu trúc, assertion ABSENT; mọi ý có/không/bình thường/nghi ngờ đều chuyển sang assertion, không để lại trong `phrase`.
- Không chứa chủ thể ("mẹ"), không chứa con số đo.

# Trường `source_text`

Câu trong mô tả có chứa biểu hiện, chép nguyên văn từng chữ, không sửa, không rút gọn. Biểu hiện nằm trong một câu dài thì chép cả câu đó.

# Kết luận và chủ thể

assertion — biểu hiện được kết luận thế nào:
- PRESENT: ghi nhận có.
- ABSENT: đã khảo sát và kết luận không có ("không thấy", "chưa thấy", "chưa ghi nhận", "bình thường").
- SUSPECTED: nghi ngờ, nghĩ tới, có thể, theo dõi, chưa khẳng định nhưng nghiêng về có.
- UNCERTAIN: không kết luận được (khảo sát hạn chế, không đánh giá được, chưa khảo sát).

assessment — lần khám này đã khảo sát được đến mức nào (đề nghị khám thêm để xác định không làm đổi mức này):
- ASSESSED: đã khảo sát và có kết luận.
- LIMITED: có khảo sát nhưng hạn chế ("khó đánh giá", "chưa thấy rõ", "chưa khảo sát đủ", "chưa thấy, hẹn khảo sát lại").
- NOT_ASSESSABLE: không thể khảo sát ("không đánh giá được", "không quan sát được", "chưa khảo sát được do tư thế").
- NOT_ASSESSED: chưa khảo sát, không nêu gì cản trở.

subject: FETUS (thai; gồm cả bánh nhau, dây rốn, nước ối, ngôi thai, cử động thai), MOTHER (bệnh hay tình trạng của mẹ), FAMILY (tiền sử gia đình: con trước, anh chị em, họ hàng).

temporality: tuổi thai lúc quan sát nếu mô tả có nêu, ví dụ "22 tuần"; không có thì để trống.

# Ví dụ minh hoạ tiêu chí

- "Thận hai bên: tăng âm, kích thước lớn" → `thận hai bên tăng âm`, `thận hai bên kích thước lớn` (PRESENT, ASSESSED).
- "Chưa ghi nhận giãn não thất" → `giãn não thất` (ABSENT, ASSESSED).
- "Nghi thiểu sản xương mũi" → `thiểu sản xương mũi` (SUSPECTED, ASSESSED).
- "Khó đánh giá bàn tay thai do tư thế thai" → `bàn tay` (UNCERTAIN, LIMITED).
- "Không thấy xương mũi" → `không thấy xương mũi` (PRESENT, ASSESSED).
- "Tim bình thường, mỏm vai liên tục" → `bất thường tim`, `bất thường mỏm vai` (ABSENT, ASSESSED).
- "Nang ống mật chủ – giãn đường mật trong gan" → hai mục: `nang ống mật chủ`, `giãn đường mật trong gan`.
- "Không thấy rõ xương mũi, nghi bất sản xương mũi" → một mục: `bất sản xương mũi` (SUSPECTED, ASSESSED).
- "Đường kính lưỡng đỉnh 52 mm, chỉ số ối 12 cm" → không có mục nào: chỉ có số đo, không có kết luận.
