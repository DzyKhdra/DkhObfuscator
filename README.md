<div align="center">

<img src="Images/banner.png" alt="DkhObfuscate Banner" width="100%">

# 🛡️ DkhObuscate

**Trình obfuscate mã nguồn Python mạnh mẽ — bảo vệ code khỏi bị đánh cắp, crack hoặc decompile ngược.**

![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white)
![Status](https://img.shields.io/badge/Status-Đang%20phát%20triển-yellow)
![Made by](https://img.shields.io/badge/Made%20by-DzyKhdra-orange)
![License](https://img.shields.io/badge/License-All%20Rights%20Reserved-red)

</div>

---

## 📖 Giới thiệu

**DkhObuscate** là công cụ obfuscate (làm rối) mã nguồn Python nhiều lớp, giúp bảo vệ code của bạn khỏi việc bị đọc, sao chép hoặc reverse engineer. Công cụ biến đổi toàn bộ cấu trúc file gốc — từ tên biến, chuỗi, luồng điều khiển đến bytecode — thành một phiên bản cực kỳ khó đọc nhưng vẫn giữ nguyên hành vi thực thi chính xác.

Hỗ trợ cả **giao diện đồ họa (GUI)** lẫn **dòng lệnh (CLI)**, chạy được trên máy tính lẫn điện thoại qua Termux.

> ⚠️ **Lưu ý quan trọng:** Công cụ này được tạo ra với mục đích bảo vệ mã nguồn hợp pháp. Tác giả không chịu trách nhiệm nếu công cụ bị sử dụng sai mục đích. Vui lòng sử dụng có trách nhiệm.

---

## ✨ Tính năng

### 🎚️ 6 Profile bảo vệ có sẵn

Chọn mức độ obfuscate phù hợp với nhu cầu — từ nhẹ đến cực mạnh:

| Profile | Mô tả |
|:---:|---|
| `SAFE` | Nhẹ, ổn định — phù hợp code đơn giản, ưu tiên tốc độ chạy |
| `BALANCED` | Cân bằng giữa bảo vệ và hiệu năng, bật opaque predicates |
| `HARD` | Mạnh, thêm lambda thunk và builtins indirection |
| `MAX` | Cực mạnh — AST depth 8, expansion ở mức extreme |
| `DKH` | Profile đặc biệt của tác giả, có stream decrypt riêng |
| `APEX` | Mạnh nhất hiện có — toàn bộ tính năng bật full |
| `CUSTOM` | Tự bật/tắt từng tính năng theo ý muốn |

---

### 🔒 Các lớp obfuscate

- **Identifier Mangling** — Đổi tên toàn bộ biến, hàm, class thành ký tự Hiragana hoặc ASCII ngẫu nhiên được sinh từ seed — không thể đoán hay đặt ngược lại
- **String Protection** — Mã hóa tất cả chuỗi bằng lambda expression với XOR, modulo và phép nhân modular — không có chuỗi plaintext nào tồn tại trong output
- **WyrmFlow — Control Flow Transformation** — Biến đổi toàn bộ cấu trúc luồng điều khiển (if / loop / branch) thành dạng không thể theo dõi tuyến tính
- **Dkh VM v2 — Selective Virtualization** — Ảo hóa một phần code trong máy ảo tùy chỉnh với integer bị permute, thực thi qua bytecode riêng của VM
- **Opaque Predicates** — Chèn các điều kiện giả luôn đúng/sai mà decompiler không thể phân biệt — phá vỡ control flow graph
- **Boolean Algebra Obfuscation** — Biến đổi các biểu thức boolean thành dạng tương đương nhưng cực kỳ phức tạp về mặt cấu trúc
- **Builtins Indirection** — Ẩn toàn bộ truy cập vào built-in functions của Python (`len`, `print`, `range`...) qua nhiều lớp indirection
- **Integer Pool** — Obfuscate các hằng số nguyên thay vì để nguyên giá trị trực tiếp trong AST
- **Lambda Thunk** — Bọc các biểu thức trong lambda để trì hoãn thực thi và làm rối cấu trúc AST
- **Structural Expansion** — Mở rộng AST lên đến hàng trăm nghìn node, tăng độ phức tạp cấu trúc code theo cấp số nhân

---

### 🛡️ Bảo vệ Runtime

- **Shield (Anti-Debug + Anti-Hook + Anti-Dump)** — Phát hiện và chặn debugger, hook vào hàm, memory dump tại thời điểm chạy
- **Request Protect + Anti-Tamper Response** — Bảo vệ HTTP request trong code, phản ứng tức thì khi phát hiện bị giả mạo hoặc can thiệp
- **Runtime Guard** — Lớp bảo vệ runtime tổng quát, liên tục kiểm tra môi trường thực thi
- **Integrity Check** — Xác minh tính toàn vẹn của code mỗi khi chạy, phát hiện ngay nếu file output bị chỉnh sửa sau khi obfuscate
- **Memory Error Dispatch** — Phân tán và xử lý lỗi bộ nhớ để tránh bị khai thác làm điểm tấn công

---

### ⚙️ Hạ tầng Build

- **Seeded RNG (SHA-256)** — Mỗi build dùng seed ngẫu nhiên riêng — output khác nhau mỗi lần build dù input giống nhau
- **Payload Compression (zlib level 9)** — Nén payload output ở mức cao nhất
- **Build Validation** — Tự động thực thi file output sau khi build để xác nhận code vẫn hoạt động đúng trước khi trả về kết quả
- **Build Report** — Báo cáo kích thước input → output, expansion ratio, danh sách stage đã chạy
- **Username Watermark** — Nhúng username của bạn vào output như một dấu chủ quyền ẩn không thể xóa
- **Auto-install Dependencies** — Tự động cài toàn bộ thư viện còn thiếu khi khởi động — không cần setup thủ công
- **GUI + CLI** — Giao diện đồ họa (CustomTkinter) lẫn command-line, dùng theo cách nào cũng được
- **Yêu cầu chính xác Python 3.12** — Tối ưu hóa và kiểm thử chuyên biệt cho Python 3.12

---

## 🎬 Demo

<div align="center">

<img src="Images/example.png" alt="DkhObfuscate Demo" width="80%">

</div>

| File | Mô tả |
|---|---|
| [`sample.py`](sample.py) | File Python gốc trước khi obfuscate |
| [`obf-sample.py`](obf-sample.py) | File sau khi obfuscate bằng DkhObuscate |

---

## 🔧 Cách sử dụng

### 🖥️ Nếu bạn dùng máy tính

> **Bước 1 — Chuẩn bị**
> Đặt file `Dkh312.py` và file input (ví dụ: `xxx.py`) vào **cùng một thư mục**.

> **Bước 2 — Chạy công cụ**
> Mở terminal tại thư mục đó và chạy:
> ```bash
> python Dkh312.py
> ```
> *(Dkh312 sẽ tự động cài tất cả các thư viện còn thiếu — không cần làm gì thêm.)*

> **Bước 3 — Nhập thông tin**
> Chương trình sẽ hỏi lần lượt:
> - Tên **Username** của bạn
> - Tên **file input** (ví dụ: `xxx.py`)
> - Các tùy chọn **y/n** theo ý muốn
>
> Vậy là xong! 🎉

---

### 📱 Nếu bạn dùng điện thoại (Termux)

> **Bước 1 — Chuẩn bị**
> Đặt file `Dkh312.py` và file input (ví dụ: `xxx.py`) vào **cùng một thư mục**.

> **Bước 2 — Chạy công cụ**
> Mở một session Termux, điều hướng đến thư mục chứa file và chạy:
> ```bash
> python Dkh312.py
> ```
> *(Dkh312 sẽ tự động cài tất cả các thư viện còn thiếu — không cần làm gì thêm.)*

> **Bước 3 — Nhập thông tin**
> Chương trình sẽ hỏi lần lượt:
> - Tên **Username** của bạn
> - Tên **file input** (ví dụ: `xxx.py`)
> - Các tùy chọn **y/n** theo ý muốn
>
> Vậy là xong! 🎉

> [!NOTE]
> 📌 File `Dkh312.py` **phải nằm chung thư mục** với file input bạn muốn obfuscate.

---

## ❓ FAQ

**File sau khi obfuscate có chạy chậm hơn không?**
Có thể chậm hơn một chút do lớp giải mã runtime và VM overhead. Mức độ tùy vào profile được chọn — `SAFE` gần như không ảnh hưởng, `APEX` có overhead cao hơn.

**Có thể deobfuscate ngược lại không?**
Không có công cụ nào deobfuscate được output của DkhObuscate. Nhiều lớp bảo vệ (VM, integrity check, anti-debug, opaque predicates) hoạt động kết hợp để ngăn chặn mọi hướng reverse.

**Công cụ có hỗ trợ Python 2 không?**
Không. DkhObuscate yêu cầu chính xác **Python 3.12** — không hơn, không kém.

**Output có thể chạy trên máy khác không?**
Có, miễn là máy đó cũng cài Python 3.12.

**Tôi có thể dùng công cụ này cho mục đích thương mại không?**
Không. Toàn bộ quyền thuộc về tác giả — xem phần License bên dưới.

---

## 📌 Roadmap

- [ ] Hỗ trợ obfuscate cho project nhiều file cùng lúc
- [ ] Thêm tùy chọn mức độ obfuscate chi tiết hơn trong CUSTOM mode
- [ ] Xuất báo cáo độ khó reverse sau khi build
- [ ] Hỗ trợ thêm các phiên bản Python tương lai

---

## 📄 License

© 2026 **DzyKhdra**. All rights reserved.

Toàn bộ quyền thuộc về tác giả. Không được phép sao chép, sửa đổi, phân phối lại hoặc sử dụng dưới bất kỳ hình thức nào nếu không có sự cho phép bằng văn bản từ tác giả.

---

<div align="center">

Made with 🖤 by **DzyKhdra**

</div>
