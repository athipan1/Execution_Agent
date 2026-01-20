# สรุปการวิเคราะห์และแนวทางการแก้ไข `ModuleNotFoundError`

เอกสารนี้สรุปปัญหา `ModuleNotFoundError: No module named 'app'` ที่เกิดขึ้นในโปรเจกต์ `execution-agent` พร้อมทั้งแนวทางการแก้ไข และคำแนะนำในการปรับปรุงโครงสร้างโปรเจกต์ให้เป็นไปตามมาตรฐานที่ดีที่สุด (Best Practices) สำหรับการใช้งานในระยะยาว

---

### 1. วิเคราะห์สาเหตุของปัญหา

ปัญหา `ModuleNotFoundError` เกิดจากความไม่สอดคล้องกันระหว่าง **ตำแหน่งที่รันโค้ด** และ **วิธีการ import module** ภายใน Docker container

- **`WORKDIR /home/appuser/app`**: Dockerfile กำหนดให้ directory การทำงานอยู่ที่ `/home/appuser/app`
- **`COPY app/ ./`**: เนื้อหาของโฟลเดอร์ `app` จากเครื่องของคุณ ถูกคัดลอกไปที่ `WORKDIR` ดังนั้นไฟล์ `main.py` จึงไปอยู่ที่ `/home/appuser/app/main.py`
- **`CMD [".../uvicorn", "main:app", ...]`**: คำสั่งนี้ถูกรันจาก `WORKDIR` (คือ `/home/appuser/app`)

เมื่อ Python เริ่มทำงาน มันจะเพิ่ม directory ที่สคริปต์ถูกรัน (`/home/appuser/app`) เข้าไปใน `sys.path` (path ที่ Python ใช้ค้นหา module)

เมื่อโค้ดใน `main.py` สั่ง `from app.models import ...` ซึ่งเป็นการ import แบบสมบูรณ์ (absolute import) Python จะพยายามหา module `app` จาก path ที่อยู่ใน `sys.path` มันจึงคาดว่าจะเจอไฟล์ที่ `/home/appuser/app/app/models.py` **ซึ่งไม่มีอยู่จริง** เพราะไฟล์ `models.py` อยู่ที่ `/home/appuser/app/models.py`

> **สรุป**: ปัญหาเกิดจากการรันแอปพลิเคชันจาก *ข้างใน* โฟลเดอร์ `app` แต่ lại ใช้คำสั่ง import เสมือนว่ารันมาจาก *ข้างนอก*

---

### 2. โครงสร้างโปรเจกต์ที่แนะนำ (Src Layout)

เพื่อแก้ปัญหานี้อย่างยั่งยืนและทำให้โปรเจกต์มีโครงสร้างที่ดี เราได้ปรับไปใช้ "Src Layout" ซึ่งเป็น Best Practice ที่ได้รับการยอมรับอย่างกว้างขวาง

**โครงสร้างใหม่:**
```
execution-agent/
├── src/
│   └── app/
│       ├── __init__.py
│       ├── main.py
│       ├── models.py
│       └── ... (ไฟล์อื่นๆ ของแอป)
├── Dockerfile
├── requirements.txt
├── pyproject.toml      # (แนะนำให้เพิ่ม)
└── tests/
    └── ...
```

**ข้อดีของ Src Layout:**
- **แยกโค้ดออกจากไฟล์ตั้งค่า**: โค้ดแอปพลิเคชันทั้งหมดจะอยู่ใน `src/` ทำให้ไม่ปนกับไฟล์ Docker, CI/CD, หรือไฟล์คอนฟิกอื่นๆ
- **แก้ปัญหา Import อย่างถาวร**: ทำให้มั่นใจได้ว่าโค้ดที่รันจะหา `app`เจอเสมอ ตราบใดที่ `src` อยู่ใน `PYTHONPATH`
- **ง่ายต่อการทำ Packaging**: หากในอนาคตต้องการสร้าง Python package จากโปรเจกต์นี้ โครงสร้างนี้จะเหมาะสมที่สุด

---

### 3. การแก้ไข Dockerfile และ docker-compose.yml

เพื่อให้ Docker image ทำงานกับ Src Layout ได้อย่างถูกต้อง เราได้แก้ไข `Dockerfile` ดังนี้:

**`Dockerfile` (ฉบับแก้ไข):**
```dockerfile
# ---- Builder Stage ----
FROM python:3.12-slim AS builder
WORKDIR /opt/venv
RUN python -m venv .
COPY requirements.prod.txt .
RUN . /opt/venv/bin/activate && pip install --no-cache-dir -r requirements.prod.txt

# ---- Final Stage ----
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# 1. เปลี่ยน WORKDIR มาอยู่ที่ /home/appuser
WORKDIR /home/appuser

COPY --from=builder /opt/venv /opt/venv
# 2. COPY โค้ดจาก src/ ในเครื่อง ไปยัง src/ ใน container
COPY --chown=appuser:appgroup src/ ./src/

# 3. (สำคัญที่สุด) ตั้งค่า PYTHONPATH ให้ Python วิ่งมาหา module ใน /home/appuser/src
ENV PYTHONPATH=/home/appuser/src

USER appuser
EXPOSE 8005
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8005/health || exit 1

# 4. เปลี่ยนคำสั่งรันเป็น app.main:app
CMD ["/opt/venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]
```

**`docker-compose.yml`:**
เนื่องจากเราได้แก้ไข `CMD` ใน `Dockerfile` ให้ถูกต้องแล้ว **คุณควรลบ `command:` ออกจาก `docker-compose.yml`** (ถ้ามี) เพื่อให้มันใช้ `CMD` จาก image แทน

**ตัวอย่างที่ควรจะเป็น:**
```yaml
services:
  execution-agent:
    build:
      context: ./execution-agent
      dockerfile: Dockerfile
    ports:
      - "8005:8005"
    # ไม่ควรมี 'command:' ที่นี่
    # ...
```

---

### 4. คำแนะนำเพิ่มเติมเพื่อการพัฒนาระยะยาว

- **Linter & Formatter (`ruff`)**: ช่วยจัดระเบียบโค้ดและหาข้อผิดพลาดเล็กๆ น้อยๆ โดยอัตโนมัติ
- **Type Checking (`mypy`)**: ช่วยตรวจสอบ Type Hint เพื่อลด bug ที่เกี่ยวกับชนิดข้อมูลผิดพลาด
- **Testing (`pytest`)**: เฟรมเวิร์คสำหรับเขียนเทสที่ใช้งานง่ายและมีประสิทธิภาพ

**วิธีติดตั้งและตั้งค่า (แนะนำ):**
1. สร้างไฟล์ `pyproject.toml` ที่ root ของ `execution-agent`
   ```toml
   [tool.ruff]
   line-length = 88
   select = ["E", "F", "W", "I", "UP"]
   ignore = []

   [tool.ruff.format]
   quote-style = "double"

   [tool.mypy]
   python_version = "3.12"
   warn_return_any = true
   ignore_missing_imports = true
   ```
2. แยก `requirements.dev.txt` สำหรับเครื่องมือพัฒนา:
   ```txt
   # requirements.dev.txt
   ruff
   mypy
   pytest
   pytest-asyncio
   httpx # สำหรับ TestClient
   ```
3. รันเครื่องมือ:
   ```bash
   # ตรวจสอบโค้ด
   ruff check src/

   # จัดฟอร์แมตโค้ด
   ruff format src/

   # ตรวจสอบ Type
   mypy src/
   ```

---

### 5. Checklist สรุปขั้นตอนการดำเนินงาน

ลำดับขั้นตอนที่คุณควรทำเพื่อแก้ปัญหาและปรับปรุงโปรเจกต์:

1.  [x] **ปรับโครงสร้าง**: สร้างโฟลเดอร์ `src` และย้าย `app` เข้าไปข้างใน
2.  [x] **อัปเดต Dockerfile**: แก้ไข `Dockerfile` ตามตัวอย่างด้านบน (เปลี่ยน `WORKDIR`, `COPY`, `ENV PYTHONPATH`, และ `CMD`)
3.  [ ] **ตรวจสอบ docker-compose.yml**: ตรวจสอบไฟล์ `docker-compose.yml` และนำ `command:` ของ `execution-agent` service ออก (หากมี)
4.  [ ] **(แนะนำ)** สร้างไฟล์ `pyproject.toml` พร้อมตั้งค่า `ruff` และ `mypy`
5.  [ ] **(แนะนำ)** สร้างไฟล์ `requirements.dev.txt` และติดตั้งเครื่องมือพัฒนา
6.  [ ] **Build และทดสอบ**: รัน `docker-compose build` และ `docker-compose up` เพื่อยืนยันว่าแอปพลิเคชันทำงานได้ปกติ
7.  [ ] **เริ่มเขียนเทส**: สร้างเทสในโฟลเดอร์ `tests/` และรันด้วย `pytest` เพื่อให้มั่นใจในคุณภาพของโค้ด
