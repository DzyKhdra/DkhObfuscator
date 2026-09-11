<div align="center">

# 🛡️ KhdraObuscate

**Trình obfuscate mã nguồn Python — bảo vệ code khỏi bị đánh cắp, crack hoặc decompile ngược.**

![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-Chưa%20xác%20định-lightgrey)
![Status](https://img.shields.io/badge/Status-Đang%20phát%20triển-yellow)
![Made by](https://img.shields.io/badge/Made%20by-DzyKhdra-orange)

</div>

---

## 📖 Giới thiệu

**KhdraObuscate** là công cụ obfuscate (làm rối) mã nguồn Python, giúp bảo vệ code của bạn khỏi việc bị đọc, sao chép hoặc reverse ngược một cách dễ dàng. Công cụ biến đổi cấu trúc file gốc thành một phiên bản khó đọc đối với con người nhưng vẫn giữ nguyên hành vi thực thi.

> ⚠️ **Lưu ý quan trọng:** Công cụ này được tạo ra với mục đích bảo vệ mã nguồn hợp pháp. Tác giả không chịu trách nhiệm nếu công cụ bị sử dụng sai mục đích (ví dụ: obfuscate mã độc, botnet, keylogger...). Vui lòng sử dụng có trách nhiệm.

---

## ✨ Tính năng

- 🔒 Làm rối tên biến, hàm, class thành các định danh khó đọc
- 🧩 Mã hóa chuỗi và các giá trị hằng số trong code
- 🐍 Hỗ trợ Python 3.12+
- ⚡ Giữ nguyên hành vi thực thi của chương trình gốc sau khi obfuscate
- 🗂️ Dễ tích hợp vào quy trình build/deploy sẵn có

*(Cập nhật thêm các tính năng cụ thể nếu có: anti-debug, watermark, chống decompile bằng bytecode, v.v.)*

---

## 🚀 Cài đặt

```bash
git clone https://github.com/<tên-user>/KhdraObuscate.git
cd KhdraObuscate
pip install -r requirements.txt
```

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

## 🎬 Demo

```
[input]  original_script.py  →  [KhdraObuscate]  →  [output]  obf_output.py
```

*(Có thể thêm ảnh/gif minh họa trước–sau, hoặc đoạn code so sánh ngắn giữa file gốc và file đã obfuscate)*

---

## ❓ FAQ

**File sau khi obfuscate có chạy chậm hơn không?**
Có thể chậm hơn một chút do lớp giải mã runtime, mức độ tùy vào độ phức tạp obfuscate.

**Có thể deobfuscate ngược lại không?**
Không có công cụ chính thức để deobfuscate. Mức độ khó reverse phụ thuộc vào kỹ thuật obfuscate được áp dụng.

**Công cụ có hỗ trợ Python 2 không?**
Không, chỉ hỗ trợ Python 3.12 trở lên.

**Tôi có thể dùng công cụ này cho mục đích thương mại không?**
Tùy thuộc vào giấy phép (license) mà bạn chọn cho repo — hiện repo chưa có license, nên mặc định giữ toàn quyền tác giả.

---

## 📌 Roadmap

- [ ] Hỗ trợ obfuscate cho project nhiều file
- [ ] Thêm tùy chọn mức độ obfuscate (light / medium / heavy)
- [ ] Xuất báo cáo độ khó reverse sau khi obfuscate

---

## 📄 License

Dự án hiện **chưa có license chính thức**. Điều này có nghĩa là mặc định mọi quyền được bảo lưu cho tác giả — người khác không được phép sao chép, sửa đổi hoặc phân phối lại nếu không có sự cho phép. Nếu muốn mở cho cộng đồng đóng góp, cân nhắc thêm license như MIT hoặc Apache 2.0.

---

<div align="center">

Made with 🖤 by **DzyKhdra**

</div>
